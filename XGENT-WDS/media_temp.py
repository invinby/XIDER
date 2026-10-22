"""Bounded XIDER-owned media temporary files and crash-leftover cleanup."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


MEDIA_TEMP_TTL_SECONDS = 24 * 60 * 60
_PREFIX = "xider-media-"
_SUFFIXES = {".png", ".jpg", ".jpeg", ".ogg", ".wav"}
_MAX_ENTRIES = 512


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction(path)) if callable(isjunction) else False


def _media_temp_root(*, create: bool = True) -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / "Library" / "Caches"
    app_root = base / "XIDER"
    root = app_root / "media-temp"
    if create:
        # Reject a redirected app-data directory before mkdir can create anything
        # through it. Check again after creation to catch a race/replacement.
        if _is_link_or_junction(app_root):
            raise OSError("XIDER app-data root is not a private directory")
        app_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _is_link_or_junction(app_root) or not app_root.is_dir():
            raise OSError("XIDER app-data root is not a private directory")
        root.mkdir(mode=0o700, exist_ok=True)
    if _is_link_or_junction(app_root) or _is_link_or_junction(root) or (root.exists() and not root.is_dir()):
        raise OSError("XIDER media-temp root is not a private directory")
    return root


def create_media_temp_file(suffix: str):
    suffix = str(suffix).lower()
    if suffix not in _SUFFIXES:
        raise ValueError("unsupported XIDER media-temp file type")
    return tempfile.NamedTemporaryFile(
        prefix=_PREFIX, suffix=suffix, dir=_media_temp_root(), delete=False,
    )


def cleanup_stale_media_temp(*, now: float | None = None) -> dict[str, int | str]:
    """Delete only expired regular media files under the fixed XIDER cache root."""
    try:
        root = _media_temp_root(create=False)
        if not root.exists():
            return {"state": "success", "scanned": 0, "deleted": 0, "skipped": 0, "failed": 0}
        if root.is_symlink() or not root.is_dir():
            raise OSError("XIDER media-temp root is not a directory")
        current_time = time.time() if now is None else float(now)
        report = {"state": "success", "scanned": 0, "deleted": 0, "skipped": 0, "failed": 0}
        with os.scandir(root) as entries:
            for index, item in enumerate(entries):
                if index >= _MAX_ENTRIES:
                    report["skipped"] += 1
                    break
                if not item.name.startswith(_PREFIX) or Path(item.name).suffix.lower() not in _SUFFIXES:
                    continue
                report["scanned"] += 1
                try:
                    metadata = item.stat(follow_symlinks=False)
                    if not item.is_file(follow_symlinks=False):
                        report["skipped"] += 1
                        continue
                    age = current_time - metadata.st_mtime
                    if age < MEDIA_TEMP_TTL_SECONDS or metadata.st_mtime > current_time + 300:
                        report["skipped"] += 1
                        continue
                    os.unlink(item.path)
                    report["deleted"] += 1
                except OSError:
                    report["failed"] += 1
        if report["failed"]:
            report["state"] = "partial"
        return report
    except OSError:
        return {"state": "error", "scanned": 0, "deleted": 0, "skipped": 0, "failed": 1}
