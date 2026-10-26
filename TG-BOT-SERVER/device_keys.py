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
