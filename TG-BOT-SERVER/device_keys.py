"""Server-side per-device message keys, stored outside the source tree."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from config import SHARED_KEY


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def key_for_device(device_id: str) -> str:
    """Use a provisioned device key, otherwise the legacy installation key.

    The directory is configured only on the bot server. A corrupt key file
    raises instead of silently falling back to the global legacy key.
    """
    if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
        raise ValueError("invalid device ID")
    root = os.environ.get("XIDER_DEVICE_KEYS_DIR", "").strip()
    if not root:
        return SHARED_KEY
    directory = Path(root)
    if not directory.is_absolute():
        raise ValueError("device key directory must be absolute")
    if not directory.is_dir():
        raise ValueError("device key directory is missing")
    path = directory / f"{device_id}.key"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
    except FileNotFoundError:
        return SHARED_KEY
    try:
        with os.fdopen(fd, "rb") as source:
            if os.name == "posix" and stat.S_IMODE(os.fstat(source.fileno()).st_mode) & 0o077:
                raise ValueError("device key permissions are too broad")
            raw = source.read(257)
    except OSError as exc:
        raise ValueError("device key unreadable") from exc
    if len(raw) > 256 or len(raw) < 32 or b"\n" in raw or b"\r" in raw:
        raise ValueError("invalid device key file")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid device key encoding") from exc


def save_device_key(device_id: str, key: str) -> None:
    """Create an enrolled key once; never silently replace a working device."""
    if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
        raise ValueError("invalid device ID")
    if not isinstance(key, str) or not 32 <= len(key) <= 256 or not key.isascii() or any(
        char in key for char in "\r\n"
    ):
        raise ValueError("invalid device key")
    root = os.environ.get("XIDER_DEVICE_KEYS_DIR", "").strip()
    directory = Path(root)
    if not root or not directory.is_absolute() or not directory.is_dir():
        raise ValueError("device key directory is unavailable")
    path = directory / f"{device_id}.key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as target:
            if os.name == "posix":
                os.fchmod(target.fileno(), 0o600)
            target.write(key.encode("ascii"))
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def remove_device_key_if_matches(device_id: str, expected_key: str) -> bool:
    """Clean up a failed enrollment without touching an older identity."""
    root = os.environ.get("XIDER_DEVICE_KEYS_DIR", "").strip()
    if not root or key_for_device(device_id) != expected_key:
        return False
    path = Path(root) / f"{device_id}.key"
    path.unlink()
    return True


def enrolled_device_ids() -> list[str]:
    """IDs whose commands must use the enrolled-device broker."""
    root = os.environ.get("XIDER_DEVICE_KEYS_DIR", "").strip()
    if not root:
        return []
    directory = Path(root)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("device key directory is unavailable")
    return sorted(
        path.name[:-4] for path in directory.glob("*.key")
        if not path.is_symlink() and _ID.fullmatch(path.name[:-4])
    )
