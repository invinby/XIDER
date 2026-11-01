"""Bounded XIDER-owned media temporary files and crash-leftover cleanup."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path


MEDIA_TEMP_TTL_SECONDS = 24 * 60 * 60
_PREFIX = "xider-media-"
_SUFFIXES = {".png", ".jpg", ".jpeg", ".ogg", ".wav"}
_MAX_ENTRIES = 512
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_QUOTA_LOCK = threading.RLock()
_RESERVATIONS: dict[str, int] = {}


class _QuotaFile:
    """File wrapper enforcing a hard size cap and releasing its reservation."""

    def __init__(self, file, max_bytes: int):
        self._file = file
        self._max_bytes = max_bytes
        self._closed = False
        self._released = False
        self._write_lock = threading.Lock()

    def write(self, data):
        with self._write_lock:
            end = self._file.tell() + len(data)
            if end > self._max_bytes:
                raise OSError("XIDER media-temp per-file byte quota exceeded")
            return self._file.write(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def truncate(self, size=None):
        requested = self._file.tell() if size is None else size
        if requested > self._max_bytes:
            raise OSError("XIDER media-temp per-file byte quota exceeded")
        return self._file.truncate(size)

    def close(self, *, retain_reservation: bool = False):
        if not self._closed:
            self._closed = True
            self._file.close()
        if not retain_reservation:
            self.release()

    def release(self):
        """Release the reserved path after an external writer has finished."""
        if not self._closed:
            self.close(retain_reservation=True)
        path = Path(self._file.name)
        with _QUOTA_LOCK:
            if self._released:
                return
            try:
                if path.is_symlink():
                    path.unlink(missing_ok=True)
                    raise OSError("XIDER media-temp path changed during external write")
                if path.exists() and path.stat().st_size > self._max_bytes:
                    path.unlink(missing_ok=True)
                    raise OSError("XIDER media-temp per-file byte quota exceeded")
            finally:
                _RESERVATIONS.pop(str(path.absolute()), None)
                self._released = True

    def __enter__(self):
        self._file.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def name(self):
        return self._file.name


def _owned_usage(root: Path) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    with os.scandir(root) as entries:
        for item in entries:
            if not item.name.startswith(_PREFIX) or Path(item.name).suffix.lower() not in _SUFFIXES:
                continue
            count += 1
            if count > _MAX_ENTRIES:
                raise OSError("XIDER media-temp entry quota exceeded")
            try:
                metadata = item.stat(follow_symlinks=False)
                if item.is_file(follow_symlinks=False):
                    total_bytes += metadata.st_size
            except OSError:
                continue
            if total_bytes > _MAX_TOTAL_BYTES:
                raise OSError("XIDER media-temp byte quota exceeded")
    return count, total_bytes


def _reserved_bytes() -> int:
    total = 0
    for raw_path, max_bytes in list(_RESERVATIONS.items()):
        path = Path(raw_path)
        try:
            if path.is_symlink() or not path.is_file():
                _RESERVATIONS.pop(raw_path, None)
                continue
            current_size = path.stat().st_size
        except OSError:
            _RESERVATIONS.pop(raw_path, None)
            continue
        total += max(0, max_bytes - current_size)
    return total


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
    if _MAX_FILE_BYTES <= 0 or _MAX_TOTAL_BYTES < _MAX_FILE_BYTES:
        raise OSError("XIDER media-temp byte quota is not available")
    with _QUOTA_LOCK:
        root = _media_temp_root()
        cleanup = cleanup_stale_media_temp()
        if cleanup.get("state") == "error":
            raise OSError("XIDER media-temp cache could not be checked safely")
        count, total_bytes = _owned_usage(root)
        if count >= _MAX_ENTRIES or total_bytes + _reserved_bytes() + _MAX_FILE_BYTES > _MAX_TOTAL_BYTES:
            raise OSError("XIDER media-temp quota exceeded")
        file = tempfile.NamedTemporaryFile(
            prefix=_PREFIX, suffix=suffix, dir=root, delete=False,
        )
        _RESERVATIONS[str(Path(file.name).absolute())] = _MAX_FILE_BYTES
        return _QuotaFile(file, _MAX_FILE_BYTES)


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
        with _QUOTA_LOCK:
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
