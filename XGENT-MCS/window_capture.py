"""Explicit owner-selected macOS window capture (no whole-display fallback)."""
from __future__ import annotations

import hashlib
import io
import json
import re

MAX_WINDOWS = 32
MAX_WINDOW_PIXELS = 40_000_000
MAX_WINDOW_BYTES = 30 * 1024 * 1024


def _clean_label(value, limit=120):
    if not isinstance(value, str):
        return ""
    value = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
    return value[:limit]


def build_window_catalog(rows):
    if not isinstance(rows, list):
        raise ValueError("Invalid window catalog")
    normalized, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid window catalog row")
        native_id, pid, bounds = row.get("native_id"), row.get("pid"), row.get("bounds")
        if (type(native_id) is not int or native_id <= 0 or native_id in seen or type(pid) is not int or pid <= 0
                or not isinstance(bounds, (tuple, list)) or len(bounds) != 4
                or any(type(value) is not int for value in bounds)):
            raise ValueError("Invalid native window metadata")
        left, top, right, bottom = bounds
        width, height = right - left, bottom - top
        title, app = _clean_label(row.get("title")), _clean_label(row.get("app"), 80)
        if not title or not app or width <= 0 or height <= 0 or width * height > MAX_WINDOW_PIXELS:
            continue
        seen.add(native_id)
        normalized.append({"native_id": native_id, "pid": pid, "title": title, "app": app, "width": width, "height": height})
    normalized.sort(key=lambda row: (row["app"].casefold(), row["title"].casefold(), row["width"], row["height"], row["native_id"]))
    normalized = normalized[:MAX_WINDOWS]
    if not normalized:
        raise ValueError("No capturable titled windows")
    digest_rows = [{key: row[key] for key in ("native_id", "pid", "title", "app", "width", "height")} for row in normalized]
    token = hashlib.sha256(json.dumps(digest_rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return {"catalog_id": token, "windows": [
        {"id": f"window-{index}", "title": row["title"], "app": row["app"], "width": row["width"], "height": row["height"]}
        for index, row in enumerate(normalized, 1)
    ]}


def resolve_window(rows, window_id, catalog_id):
    if not isinstance(window_id, str) or not re.fullmatch(r"window-[1-9][0-9]?", window_id):
        raise ValueError("Invalid window selection")
    catalog = build_window_catalog(rows)
    if not isinstance(catalog_id, str) or catalog["catalog_id"] != catalog_id:
        raise ValueError("Window catalog changed; refresh it")
    index = int(window_id.split("-", 1)[1]) - 1
    ordered = sorted(rows, key=lambda row: (_clean_label(row.get("app"), 80).casefold(), _clean_label(row.get("title")).casefold(),
                                             row["bounds"][2] - row["bounds"][0], row["bounds"][3] - row["bounds"][1], row["native_id"]))
    valid = [row for row in ordered if _clean_label(row.get("title")) and _clean_label(row.get("app"), 80)
             and 0 < row["bounds"][2] - row["bounds"][0] and 0 < row["bounds"][3] - row["bounds"][1]
             and (row["bounds"][2] - row["bounds"][0]) * (row["bounds"][3] - row["bounds"][1]) <= MAX_WINDOW_PIXELS]
    if index >= min(len(valid), MAX_WINDOWS):
        raise ValueError("Invalid window selection")
    return valid[index]["native_id"]


def encode_window_image(image, window_id, title, app):
    width, height = getattr(image, "size", (0, 0))
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0 or width * height > MAX_WINDOW_PIXELS:
        raise ValueError("Captured window exceeds pixel limit")
    output = io.BytesIO()
    with image.convert("RGB") as clean:
        clean.save(output, format="PNG", optimize=True)
    encoded = output.getvalue()
    if not encoded or len(encoded) > MAX_WINDOW_BYTES:
        raise ValueError("Captured window exceeds byte limit")
    return encoded, {"id": window_id, "title": _clean_label(title), "app": _clean_label(app, 80), "width": width, "height": height}


def _enumerate_native_windows():
    import Quartz
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    info_rows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
    rows = []
    for item in info_rows:
        if not isinstance(item, dict) or int(item.get(Quartz.kCGWindowLayer, -1)) != 0:
            continue
        native_id = item.get(Quartz.kCGWindowNumber)
        pid = item.get(Quartz.kCGWindowOwnerPID)
        bounds = item.get(Quartz.kCGWindowBounds)
        if type(native_id) is not int or type(pid) is not int or not isinstance(bounds, dict):
            continue
        left, top = int(bounds.get("X", 0)), int(bounds.get("Y", 0))
        width, height = int(bounds.get("Width", 0)), int(bounds.get("Height", 0))
        if width <= 0 or height <= 0:
            continue
        rows.append({"native_id": native_id, "pid": pid,
                     "title": item.get(Quartz.kCGWindowName) or item.get(Quartz.kCGWindowOwnerName) or "",
                     "app": item.get(Quartz.kCGWindowOwnerName) or "application",
                     "bounds": (left, top, left + width, top + height)})
        if len(rows) >= 512:
            break
    return rows


def get_window_catalog():
    return build_window_catalog(_enumerate_native_windows())


def _capture_native_window(native_id):
    import Quartz
    from PIL import Image
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
        native_id, Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise PermissionError("macOS did not return an image for the selected window")
    width, height = int(Quartz.CGImageGetWidth(image)), int(Quartz.CGImageGetHeight(image))
    if width <= 0 or height <= 0 or width * height > MAX_WINDOW_PIXELS or int(Quartz.CGImageGetBitsPerPixel(image)) != 32:
        raise ValueError("Unsupported macOS window image format")
    stride = int(Quartz.CGImageGetBytesPerRow(image))
    raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image)))
    if stride < width * 4 or len(raw) < stride * height:
        raise ValueError("Invalid macOS window image data")
    bitmap_info = int(Quartz.CGImageGetBitmapInfo(image))
    byte_order = bitmap_info & int(Quartz.kCGBitmapByteOrderMask)
    raw_mode = "BGRA" if byte_order == int(Quartz.kCGBitmapByteOrder32Little) else "ARGB"
    return Image.frombytes("RGBA", (width, height), raw[:stride * height], "raw", raw_mode, stride, 1)


def capture_window(window_id, catalog_id):
    rows = _enumerate_native_windows()
    selected_id = resolve_window(rows, window_id, catalog_id)
    row = next(item for item in rows if item["native_id"] == selected_id)
    image = _capture_native_window(selected_id)
    try:
        return encode_window_image(image, window_id, row["title"], row["app"])
    finally:
        image.close()
