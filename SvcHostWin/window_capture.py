"""Explicit owner-selected Windows top-level window capture."""
from __future__ import annotations

import hashlib
import io
import json
import os
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
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid window catalog row")
        native_id, pid = row.get("native_id"), row.get("pid")
        bounds = row.get("bounds")
        if (type(native_id) is not int or native_id <= 0 or native_id in seen
                or type(pid) is not int or pid <= 0
                or not isinstance(bounds, (tuple, list)) or len(bounds) != 4
                or any(type(value) is not int for value in bounds)):
            raise ValueError("Invalid native window metadata")
        left, top, right, bottom = bounds
        width, height = right - left, bottom - top
        title, app = _clean_label(row.get("title")), _clean_label(row.get("app"), 80)
        if not title or not app or width <= 0 or height <= 0 or width * height > MAX_WINDOW_PIXELS:
            continue
        seen.add(native_id)
        normalized.append({"native_id": native_id, "pid": pid, "title": title, "app": app,
                           "width": width, "height": height})
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
    from PIL import Image
    output = io.BytesIO()
    with image.convert("RGB") as clean:
        clean.save(output, format="PNG", optimize=True)
    encoded = output.getvalue()
    if not encoded or len(encoded) > MAX_WINDOW_BYTES:
        raise ValueError("Captured window exceeds byte limit")
    return encoded, {"id": window_id, "title": _clean_label(title), "app": _clean_label(app, 80), "width": width, "height": height}


def _enumerate_native_windows():
    if os.name != "nt":
        raise OSError("Windows window capture is unsupported on this platform")
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    user32.IsWindowVisible.argtypes, user32.IsWindowVisible.restype = [wintypes.HWND], wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes, user32.GetWindowTextLengthW.restype = [wintypes.HWND], ctypes.c_int
    user32.GetWindowTextW.argtypes, user32.GetWindowTextW.restype = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int
    user32.GetWindowRect.argtypes, user32.GetWindowRect.restype = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes, user32.GetWindowThreadProcessId.restype = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD
    user32.EnumWindows.argtypes, user32.EnumWindows.restype = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM], wintypes.BOOL
    kernel32.OpenProcess.argtypes, kernel32.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes, kernel32.QueryFullProcessImageNameW.restype = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
    kernel32.CloseHandle.argtypes, kernel32.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    rows = []
    rect_t = wintypes.RECT
    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = min(512, max(0, user32.GetWindowTextLengthW(hwnd)))
        if not length:
            return True
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        rect = rect_t()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        app = "application"
        if process:
            try:
                image_buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(image_buf))
                if kernel32.QueryFullProcessImageNameW(process, 0, image_buf, ctypes.byref(size)):
                    app = image_buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
            finally:
                kernel32.CloseHandle(process)
        bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
            rows.append({"native_id": int(hwnd), "pid": int(pid.value), "title": title_buf.value,
                         "app": app, "bounds": bounds})
        return len(rows) < 512
    callback = enum_cb(visit)
    if not user32.EnumWindows(callback, 0):
        raise OSError(ctypes.get_last_error(), "Could not enumerate windows")
    return rows


def get_window_catalog():
    return build_window_catalog(_enumerate_native_windows())


def _capture_native_window(hwnd, expected_size):
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    width, height = expected_size
    if width * height > MAX_WINDOW_PIXELS:
        raise ValueError("Window exceeds pixel limit")
    user32, gdi32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [wintypes.HWND, wintypes.HDC], ctypes.c_int
    user32.PrintWindow.argtypes, user32.PrintWindow.restype = [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes, gdi32.CreateCompatibleBitmap.restype = [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [wintypes.HGDIOBJ], wintypes.BOOL
    gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [wintypes.HDC], wintypes.BOOL
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise OSError("Could not access desktop device context")
    memory_dc = bitmap = old_bitmap = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not memory_dc or not bitmap:
            raise OSError("Could not allocate window capture buffer")
        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            raise OSError("Windows could not render the selected window")
        class Header(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        class Info(ctypes.Structure):
            _fields_ = [("bmiHeader", Header), ("bmiColors", wintypes.DWORD * 3)]
        info = Info()
        info.bmiHeader.biSize = ctypes.sizeof(Header)
        info.bmiHeader.biWidth, info.bmiHeader.biHeight = width, -height
        info.bmiHeader.biPlanes, info.bmiHeader.biBitCount, info.bmiHeader.biCompression = 1, 32, 0
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi32.SelectObject(memory_dc, old_bitmap)
        old_bitmap = None
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                    wintypes.LPVOID, ctypes.c_void_p, wintypes.UINT]
        gdi32.GetDIBits.restype = ctypes.c_int
        if gdi32.GetDIBits(screen_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            raise OSError("Windows could not read the selected window image")
        return Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
    finally:
        if old_bitmap and memory_dc:
            gdi32.SelectObject(memory_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def capture_window(window_id, catalog_id):
    rows = _enumerate_native_windows()
    selected_id = resolve_window(rows, window_id, catalog_id)
    row = next(item for item in rows if item["native_id"] == selected_id)
    image = _capture_native_window(selected_id, (row["bounds"][2] - row["bounds"][0], row["bounds"][3] - row["bounds"][1]))
    try:
        return encode_window_image(image, window_id, row["title"], row["app"])
    finally:
        image.close()
