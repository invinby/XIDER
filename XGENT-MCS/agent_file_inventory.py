"""Owner-facing, bounded metadata inventory for the installed macOS XIDER stack.

This module never returns file contents and never scans arbitrary directories.
Hashes are observations, not integrity proof: without a signed per-file release
manifest the result explicitly says ``unverified``.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any


SCHEMA = "xider.agent-files.v1"
MAX_FILES_PER_COMPONENT = 64
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_READ_BYTES = 8192
_VERSION = re.compile(r"^[0-9]{1,6}(?:\.[0-9]{1,6}){1,2}$")

# Explicit owner-readable source/artifact allowlist. No config, logs, secrets,
# binaries, generated user data, or caller-provided paths belong here.
_READABLE_FILE_IDS = {
    "agent_mcs": "xgent_mcs.py",
    "agent_layers": "xider_layers.py",
    "agent_req": "requirements.txt",
    "agent_start": "start_agent.sh",
    "guardian_py": "xider_guardian.py",
    "guardian_start": "start_guardian.sh",
    "safari_start": "start_safari.sh",
    "siri_start": "start_siri.sh",
}
_READABLE_FILE_IDS_BY_PATH = {path: file_id for file_id, path in _READABLE_FILE_IDS.items()}

# Only repo-owned, top-level runtime artifacts are considered. In particular,
# .env, key material, user data, logs, caches, and venv packages are excluded.
_AGENT_FILES = (
    "XIDER.app/Contents/MacOS/XGENT-MCS",
    "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
    "release_signature.py", "update_package.py", "guardian_recovery.py",
    "command_module_loader.py", "command_signing.py", "command_replay.py",
    "shell_execution.py", "setup_mac.py", "location_adapter.py",
    "camera_quality.py", "camera_devices.py", "camera_series.py",
    "microphone_devices.py", "microphone_level.py", "audio_output_control.py",
    "media_temp.py", "media_history.py", "media_request_control.py",
    "display_contact_sheet.py", "window_capture.py", "requirements.txt",
)
_GUARDIAN_FILES = (
    "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper",
    "xider_guardian.py", "guardian_recovery.py", "release_signature.py",
    "update_package.py", "start_guardian.sh",
)
_SAFARI_FILES = (
    "XIDER-Layers.app/Contents/MacOS/XIDER-Layers",
    "xider_layers.py", "start_safari.sh",
)
_SIRI_FILES = (
    "XIDER-Layers.app/Contents/MacOS/XIDER-Layers",
    "xider_layers.py", "start_siri.sh",
)
_EXECUTABLE_FILES = frozenset({
    "XIDER.app/Contents/MacOS/XGENT-MCS",
    "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper",
    "XIDER-Layers.app/Contents/MacOS/XIDER-Layers",
})
_ENTRYPOINTS = {
    "agent": {
        "source": "xgent_mcs.py",
        "compiled": "XIDER.app/Contents/MacOS/XGENT-MCS",
    },
    "guard_keeper": {
        "source": "xider_guardian.py",
        "compiled": "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper",
    },
    "safari": {
        "source": "xider_layers.py",
        "compiled": "XIDER-Layers.app/Contents/MacOS/XIDER-Layers",
    },
    "siri": {
        "source": "xider_layers.py",
        "compiled": "XIDER-Layers.app/Contents/MacOS/XIDER-Layers",
    },
}


def _utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _version(value: Any) -> str | None:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def _safe_default_root() -> Path:
    return Path(__file__).parent


def _trusted_root(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        value = _safe_default_root()
    raw = Path(value).expanduser()
    try:
        if raw.is_symlink():
            return None
        root = raw.resolve(strict=True)
        if not root.is_dir():
            return None
        return root
    except (OSError, RuntimeError):
        return None


def _contained_regular_file(root: Path, relative: str) -> tuple[Path | None, str | None]:
    """Return a safe contained file path and a small status reason."""
    try:
        candidate = root / relative
        # Reject symlinks at every component, including the leaf. The paths in
        # the allowlist are intentionally shallow and do not require symlinks.
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                return None, "symlink_skipped"
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            return None, "outside_root"
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode):
            return None, "not_regular_file"
        if info.st_size > MAX_FILE_BYTES:
            return None, "file_too_large"
        return resolved, None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, RuntimeError, ValueError):
        return None, "inaccessible"


def read_allowlisted_text_file(file_id: Any, root: str | os.PathLike[str] | None) -> dict[str, Any]:
    """Read one small, fixed, repo-owned text file without following symlinks."""
    result: dict[str, Any] = {
        "file_id": file_id if isinstance(file_id, str) else None,
        "state": "not_allowed",
        "relative_path": None,
        "content": "",
        "truncated": False,
    }
    if not isinstance(file_id, str) or file_id not in _READABLE_FILE_IDS:
        return result

    relative = _READABLE_FILE_IDS[file_id]
    result["relative_path"] = relative
    trusted = _trusted_root(root)
    if trusted is None:
        result["state"] = "unavailable"
        return result

    path, problem = _contained_regular_file(trusted, relative)
    if path is None:
        result["state"] = "unavailable"
        return result
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                return {**result, "state": "unavailable"}
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_READ_BYTES + 1)
        finally:
            os.close(fd)
        truncated = len(raw) > MAX_READ_BYTES
        raw = raw[:MAX_READ_BYTES]
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            # A byte cap may split only the final code point. Invalid bytes
            # elsewhere mean the supposedly textual artifact is unavailable.
            if truncated and exc.end == len(raw):
                content = raw[:exc.start].decode("utf-8", errors="strict")
            else:
                raise
        result.update(state="available", content=content, truncated=truncated)
    except (OSError, UnicodeError, ValueError):
        result["state"] = "unavailable"
    return result


def _hash_regular_file(root: Path, relative: str) -> dict[str, Any] | None:
    path, problem = _contained_regular_file(root, relative)
    if path is None:
        if problem == "missing":
            return None
        return {
            "path": str(root / relative), "size_bytes": None, "sha256": None,
            "signature_state": "unknown", "integrity_state": "unverified",
            "evidence": problem or "unavailable",
        }

    digest = hashlib.sha256()
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_FILE_BYTES:
                return {
                    "path": str(path), "size_bytes": None, "sha256": None,
                    "signature_state": "unknown", "integrity_state": "unverified",
                    "evidence": "not_regular_or_too_large",
                }
            with os.fdopen(fd, "rb", closefd=False) as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
        finally:
            os.close(fd)
        size = opened.st_size
    except (OSError, ValueError):
        return {
            "path": str(path), "size_bytes": None, "sha256": None,
            "signature_state": "unknown", "integrity_state": "unverified",
            "evidence": "read_failed",
        }

    executable = path.suffix == ".app-executable" or "/Contents/MacOS/" in path.as_posix()
    signature_state = "not_applicable"
    signature_evidence = "source_or_data_file"
    if executable:
        signature_state, signature_evidence = _codesign_state(path)
    return {
        "path": str(path), "size_bytes": size, "sha256": digest.hexdigest(),
        "signature_state": signature_state,
        "integrity_state": "unverified",
        "evidence": "no_signed_per_file_manifest",
        "signature_evidence": signature_evidence,
    }


def _codesign_state(path: Path) -> tuple[str, str]:
    if sys.platform != "darwin":
        return "unsupported", "codesign_requires_macos"
    codesign = shutil.which("codesign")
    if not codesign:
        return "unknown", "codesign_not_found"
    try:
        result = subprocess.run(
            [codesign, "--verify", "--strict", str(path)],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", "codesign_check_failed"
    if result.returncode == 0:
        return "verified", "codesign_verify_exit_0"
    detail = (result.stderr or result.stdout or "").lower()
    if "not signed" in detail or "code object is not signed" in detail:
        return "unsigned", "codesign_reports_unsigned"
    return "invalid", "codesign_verify_failed"


def _runtime_kind() -> str:
    executable = Path(sys.executable)
    compiled = (
        bool(getattr(sys, "frozen", False))
        or bool(globals().get("__compiled__"))
        or "/Contents/MacOS/" in executable.as_posix()
    )
    return "compiled" if compiled else "source"


def _expected_in_runtime(relative: str, runtime_kind: str) -> bool:
    if relative in _EXECUTABLE_FILES:
        return runtime_kind == "compiled"
    return runtime_kind == "source"


def _inventory_file(root: Path, relative: str, runtime_kind: str) -> dict[str, Any]:
    entry = _hash_regular_file(root, relative)
    if entry is not None:
        return {"name": relative, "state": "present" if entry.get("sha256") else "unavailable", **entry}

    candidate = root / relative
    expected = _expected_in_runtime(relative, runtime_kind)
    state = "missing" if expected else "unsupported"
    evidence = "Allowlisted file is missing." if expected else "This file is not part of this runtime format."
    return {
        "name": relative,
        "path": str(candidate),
        "state": state,
        "size_bytes": None,
        "sha256": None,
        "signature_state": "unknown",
        "integrity_state": "unverified",
        "evidence": evidence,
    }


def _component(root: Path | None, component_id: str, version: str | None,
               install_path: str | None, relative_files: tuple[str, ...],
               runtime_kind: str) -> dict[str, Any]:
    if root is None:
        return {
            "id": component_id, "version": None, "install_path": None,
            "state": "unknown", "build_mode": "unknown", "obfuscation_state": "unknown",
            "files": [], "evidence": "install_root_unavailable",
        }
    entries = []
    present = set()
    for relative in relative_files[:MAX_FILES_PER_COMPONENT]:
        entry = _inventory_file(root, relative, runtime_kind)
        file_id = _READABLE_FILE_IDS_BY_PATH.get(relative)
        if file_id:
            entry["file_id"] = file_id
        entries.append(entry)
        if entry["state"] == "present":
            present.add(relative)
    entrypoint = _ENTRYPOINTS[component_id][runtime_kind]
    core_present = entrypoint in present
    expected = [entry for entry in entries if _expected_in_runtime(entry["name"], runtime_kind)]
    expected_present = sum(entry["state"] == "present" for entry in expected)
    has_unavailable = any(entry["state"] == "unavailable" for entry in expected)
    state = (
        "present" if core_present and expected and expected_present == len(expected)
        else "partial" if expected_present or has_unavailable
        else "missing"
    )
    if len(relative_files) > MAX_FILES_PER_COMPONENT:
        state = "partial"
    return {
        "id": component_id,
        "version": version if entrypoint in present else None,
        "install_path": install_path or str(root),
        "state": state,
        "files": entries,
        "build_mode": "nuitka_compiled" if runtime_kind == "compiled" else "python_source",
        "obfuscation_state": "unknown" if runtime_kind == "compiled" else "not_applied",
        "evidence": "bounded_allowlist; file_contents_not_returned",
    }


def collect_agent_file_inventory(
    install_root: str | os.PathLike[str] | None = None,
    *,
    agent_version: str | None = None,
    guardian_version: str | None = None,
) -> dict[str, Any]:
    """Return bounded owner-visible metadata for known installed XIDER artifacts.

    Missing optional components are reported explicitly. Files are SHA-256
    hashed only after containment/type checks. Hashes do not prove integrity;
    this installation currently has no signed per-file inventory manifest.
    """
    root = _trusted_root(install_root)
    if agent_version is None:
        try:
            text = (root / "config.py").read_text(encoding="utf-8", errors="replace") if root else ""
            match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            agent_version = _version(match.group(1)) if match else None
        except OSError:
            agent_version = None
    agent_version = _version(agent_version)
    guardian_version = _version(guardian_version) or agent_version
    runtime_kind = _runtime_kind()
    layers_version = None
    if root:
        try:
            text = (root / "xider_layers.py").read_text(encoding="utf-8", errors="replace")
            match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            layers_version = _version(match.group(1)) if match else None
        except OSError:
            pass

    components = [
        _component(root, "agent", agent_version, str(root) if root else None, _AGENT_FILES, runtime_kind),
        _component(root, "guard_keeper", guardian_version, str(root) if root else None, _GUARDIAN_FILES, runtime_kind),
        _component(root, "safari", layers_version, str(root / "XIDER-Layers.app") if root else None, _SAFARI_FILES, runtime_kind),
        _component(root, "siri", layers_version, str(root / "XIDER-Layers.app") if root else None, _SIRI_FILES, runtime_kind),
    ]
    available = sum(item["state"] == "present" for item in components)
    partial = sum(item["state"] == "partial" for item in components)
    state = "available" if available == len(components) else "partial" if available or partial else "missing"
    return {
        "schema": SCHEMA,
        "state": state,
        "generated_at": _utc_now(),
        "components": components,
        "protection": {
            "obfuscation_state": "unknown" if runtime_kind == "compiled" else "not_applied",
            "integrity_state": "unverified",
            "evidence": (
                "Compiled package status is not verified by this local file view; check the signed release inventory."
                if runtime_kind == "compiled" else
                "source_runtime_no_separate_obfuscation_applied"
            ),
        },
    }
