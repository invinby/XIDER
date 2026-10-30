"""Bounded, read-only inventory of known XIDER Windows installation files.

The response contains metadata only. It never reads or returns configuration,
keys, logs, or arbitrary file contents. Without a trusted signed per-file
manifest, hashes are observations and integrity remains explicitly unverified.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "xider.agent-files.v1"
GUARDIAN_VERSION = "1.0-windows"
MAX_FILES_PER_COMPONENT = 64
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_READ_BYTES = 8 * 1024
TEXT_FILE_SCHEMA = "xider.agent-file-content.v1"

# Fixed allowlist: do not recursively enumerate an installation directory.
_AGENT_FILES = (
    "XGENT-WDS.exe",
    "xgent_wds.py",
    "config.py",
    "version_info.txt",
    "install_agent.ps1",
    "requirements.txt",
    "crypto.py",
    "xgencrypto.py",
    "agent_file_inventory.py",
)
_GUARDIAN_FILES = (
    "XIDER-Guard-Keeper.exe",
    "xider_guardian_wds.py",
    "install_guardian.ps1",
)
_SOURCE_ONLY_FILES = frozenset({
    *_AGENT_FILES[1:],
    *_GUARDIAN_FILES[1:],
})
_EXECUTABLE_BY_COMPONENT = {
    "agent": {"source": "xgent_wds.py", "compiled": "XGENT-WDS.exe"},
    "guard_keeper": {"source": "xider_guardian_wds.py", "compiled": "XIDER-Guard-Keeper.exe"},
}
# Stable opaque IDs are the only identifiers accepted by the content reader.
# Keep this deliberately narrower than the metadata inventory: configuration,
# scripts, logs, keys, and arbitrary paths are never readable through this API.
_READABLE_TEXT_FILES = {
    "agent-version": "version_info.txt",
}
_SECRET_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "authorized_keys"}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _default_install_root() -> Path:
    executable = Path(sys.executable)
    if executable.name.casefold() in {"xgent-wds.exe", "xider-guard-keeper.exe"}:
        return executable.parent
    return Path(__file__).parent


def _safe_root(value: str | os.PathLike[str] | None) -> tuple[Path | None, str | None]:
    candidate = _default_install_root() if value is None else Path(value)
    try:
        # Reject a reparse-point root itself. The resolved root anchors every
        # later containment check for the fixed allowlist.
        root_stat = candidate.lstat()
        if _is_reparse(root_stat):
            return None, "Install root is a symlink or reparse point."
        root = candidate.resolve(strict=True)
        if not root.is_dir():
            return None, "Install root is not a directory."
        return root, None
    except (OSError, RuntimeError, ValueError):
        return None, "Install root is missing or inaccessible."


def _contained_regular_file(root: Path, relative_name: str) -> Path | None:
    name = Path(relative_name)
    if name.name.casefold() in _SECRET_NAMES or name.is_absolute() or ".." in name.parts:
        return None
    candidate = root / name
    try:
        info = candidate.lstat()
        if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        # Re-check the resolved target after path containment.
        resolved_info = resolved.stat()
        if (_is_reparse(resolved_info) or not stat.S_ISREG(resolved_info.st_mode)
                or resolved_info.st_size > MAX_FILE_BYTES):
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def read_allowlisted_text_file(
    file_id: object,
    install_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Read a small, fixed set of UTF-8 text files without following links."""
    relative_name = _READABLE_TEXT_FILES.get(file_id) if isinstance(file_id, str) else None
    if relative_name is None:
        return {
            "schema": TEXT_FILE_SCHEMA, "file_id": None, "relative_path": None,
            "state": "not_allowed", "content": "", "truncated": False,
        }

    root, _error = _safe_root(install_root)
    if root is None:
        return {
            "schema": TEXT_FILE_SCHEMA, "file_id": file_id, "relative_path": relative_name,
            "state": "unavailable", "content": "", "truncated": False,
        }

    path = _contained_regular_file(root, relative_name)
    if path is None:
        return {
            "schema": TEXT_FILE_SCHEMA, "file_id": file_id, "relative_path": relative_name,
            "state": "unavailable", "content": "", "truncated": False,
        }
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (_is_reparse(opened) or not stat.S_ISREG(opened.st_mode)
                    or opened.st_size > MAX_FILE_BYTES):
                raise OSError("not a readable regular file")
            raw = stream.read(MAX_READ_BYTES + 1)
    except (OSError, PermissionError, ValueError):
        return {
            "schema": TEXT_FILE_SCHEMA, "file_id": file_id, "relative_path": relative_name,
            "state": "unavailable", "content": "", "truncated": False,
        }

    truncated = len(raw) > MAX_READ_BYTES
    raw = raw[:MAX_READ_BYTES]
    content = raw.decode("utf-8", errors="replace")
    while len(content.encode("utf-8")) > MAX_READ_BYTES:
        content = content[:-1]
        truncated = True
    return {
        "schema": TEXT_FILE_SCHEMA, "file_id": file_id, "relative_path": relative_name,
        "state": "available", "content": content, "truncated": truncated,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _authenticode_status(path: Path) -> tuple[str, str]:
    """Return truthful Windows Authenticode state without opening a console."""
    if os.name != "nt":
        return "unsupported", "Authenticode is available only on Windows."
    if path.suffix.casefold() != ".exe":
        return "not_applicable", "This file type is not checked for Authenticode here."
    script = "$s=Get-AuthenticodeSignature -LiteralPath $args[0]; [Console]::Out.Write($s.Status)"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
            capture_output=True, text=True, timeout=8, check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", "Could not run the Windows Authenticode verifier."
    status = (result.stdout or "").strip()
    if result.returncode != 0 or not status:
        return "unknown", "The Windows Authenticode verifier returned no status."
    if status.casefold() == "valid":
        return "verified", "Get-AuthenticodeSignature reported Valid."
    if status.casefold() == "notsigned":
        return "unsigned", "Get-AuthenticodeSignature reported NotSigned."
    return "invalid", f"Get-AuthenticodeSignature reported {status[:80]}."


def _file_record(root: Path, relative_name: str) -> dict[str, Any] | None:
    path = _contained_regular_file(root, relative_name)
    if path is None:
        return None
    try:
        info = path.stat()
        signature_state, signature_evidence = _authenticode_status(path)
        digest = _sha256_file(path)
    except (OSError, PermissionError):
        return {
            "path": str(root / relative_name),
            "size_bytes": None,
            "sha256": None,
            "signature_state": "unknown",
            "integrity_state": "unverified",
            "evidence": "File is inaccessible; no contents were returned.",
        }
    record = {
        "path": str(path),
        "size_bytes": info.st_size,
        "sha256": digest,
        "signature_state": signature_state,
        "integrity_state": "unverified",
        "evidence": (
            f"SHA-256 observed locally; {signature_evidence} "
            "No trusted signed per-file manifest is installed."
        ),
    }
    file_id = next((key for key, value in _READABLE_TEXT_FILES.items() if value == relative_name), None)
    if file_id is not None:
        record["file_id"] = file_id
    return record


def _runtime_kind() -> str:
    executable_name = Path(sys.executable).name.casefold()
    return "compiled" if executable_name in {"xgent-wds.exe", "xider-guard-keeper.exe"} else "source"


def _inventory_file(root: Path, relative_name: str, runtime_kind: str) -> dict[str, Any]:
    """Return one explicit status row for every fixed allowlist entry."""
    record = _file_record(root, relative_name)
    if record is not None:
        return {
            "name": relative_name,
            "state": "present" if record.get("sha256") else "unavailable",
            **record,
        }

    candidate = root / relative_name
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        supported = (
            relative_name in _SOURCE_ONLY_FILES
            if runtime_kind == "compiled"
            else relative_name not in {"XGENT-WDS.exe", "XIDER-Guard-Keeper.exe"}
        )
        state = "missing" if supported else "unsupported"
        evidence = "Allowlisted file is missing." if supported else "This file is not part of this runtime format."
    except OSError:
        state = "unavailable"
        evidence = "Allowlisted file could not be inspected."
    else:
        # A symlink, reparse point, non-regular file, oversized file, or a file
        # that changed during inspection is present but unsafe to inspect.
        state = "unavailable"
        evidence = "Allowlisted path is not a safe, readable regular file."

    return {
        "name": relative_name,
        "path": str(candidate),
        "state": state,
        "size_bytes": None,
        "sha256": None,
        "signature_state": "unknown",
        "integrity_state": "unverified",
        "evidence": evidence,
    }


def _source_version(root: Path) -> str | None:
    config = _contained_regular_file(root, "config.py")
    if config is None:
        return None
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^VERSION\s*=\s*[\'\"]([^\'\"\r\n]{1,40})[\'\"]', text, re.MULTILINE)
    return match.group(1) if match else None


def _component(root: Path, component_id: str, names: tuple[str, ...], version: str | None,
               runtime_kind: str) -> dict[str, Any]:
    files = [_inventory_file(root, name, runtime_kind) for name in names[:MAX_FILES_PER_COMPONENT]]
    applicable = [row for row in files if row["state"] != "unsupported"]
    present = sum(row["state"] == "present" for row in applicable)
    state = (
        "present" if applicable and present == len(applicable)
        else "partial" if present or any(row["state"] == "unavailable" for row in applicable)
        else "absent"
    )
    entrypoint = _EXECUTABLE_BY_COMPONENT[component_id][runtime_kind]
    entrypoint_present = any(row["name"] == entrypoint and row["state"] == "present" for row in files)
    return {
        "id": component_id,
        "state": state,
        "version": version if entrypoint_present else None,
        "install_path": str(root) if present else None,
        "files": files,
    }


def collect_agent_file_inventory(
    install_root: str | os.PathLike[str] | None = None,
    *,
    agent_version: str | None = None,
    guardian_version: str | None = None,
) -> dict[str, Any]:
    """Collect bounded metadata for the XIDER Agent and Guard Keeper only."""
    generated_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    root, error = _safe_root(install_root)
    if root is None:
        return {
            "schema": SCHEMA,
            "state": "error",
            "generated_at": generated_at,
            "error": error,
            "components": [
                {"id": "agent", "state": "unknown", "version": None, "install_path": None, "files": []},
                {"id": "guard_keeper", "state": "unknown", "version": None, "install_path": None, "files": []},
            ],
        }

    runtime_kind = _runtime_kind()
    actual_agent_version = agent_version or _source_version(root)
    actual_guardian_version = guardian_version or GUARDIAN_VERSION
    agent = _component(root, "agent", _AGENT_FILES, actual_agent_version, runtime_kind)
    guardian = _component(root, "guard_keeper", _GUARDIAN_FILES, actual_guardian_version, runtime_kind)
    states = {item["state"] for item in (agent, guardian)}
    return {
        "schema": SCHEMA,
        "state": "available" if states == {"present"} else "partial" if "present" in states or "partial" in states else "missing",
        "generated_at": generated_at,
        "components": [agent, guardian],
        "protection": {
            "obfuscation_state": "unknown",
            "integrity_state": "unverified",
            "evidence": "No trusted signed per-file manifest is installed.",
        },
    }
