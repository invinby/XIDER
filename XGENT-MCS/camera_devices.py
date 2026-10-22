"""Name cameras locally and bind selections to the current OpenCV catalog."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata


def _enumerate_platform_cameras():
    from cv2_enumerate_cameras import enumerate_cameras

    return enumerate_cameras()


def _safe_name(value) -> str:
    raw = str(value or "")
    cleaned = "".join(
        " " if ch.isspace() else ch
        for ch in raw
        if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )
    cleaned = " ".join(cleaned.split())[:64].strip()
    return cleaned or "Camera"


def _camera_id(index: int, backend: int, name: str, path: str) -> str:
    identity = json.dumps([index, backend, name, path], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def list_cameras() -> list[dict]:
    """Return bounded rows; device paths and USB identifiers never leave process."""
    result = []
    seen = set()
    for info in _enumerate_platform_cameras():
        try:
            index = int(info.index)
            backend = int(info.backend)
            if index < 0 or backend < 0:
                continue
            name = _safe_name(info.name)
            path = str(getattr(info, "path", "") or "")[:512]
            camera_id = _camera_id(index, backend, name, path)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        if camera_id in seen:
            continue
        seen.add(camera_id)
        result.append({"camera_id": camera_id, "name": name, "index": index, "backend": backend})
    return sorted(result, key=lambda row: (row["name"].casefold(), row["backend"], row["index"]))[:16]


def resolve_camera(camera_id: str, cameras: list[dict] | None = None) -> dict:
    if not isinstance(camera_id, str) or re.fullmatch(r"[0-9a-f]{16}", camera_id) is None:
        raise ValueError("invalid camera selection")
    for camera in list_cameras() if cameras is None else cameras:
        if isinstance(camera, dict) and camera.get("camera_id") == camera_id:
            return camera
    raise LookupError("camera selection is stale")
