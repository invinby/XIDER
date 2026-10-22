"""Explicitly selected, bounded display contact-sheet helpers."""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import re
import sys


MAX_SELECTED_DISPLAYS = 4
MAX_CATALOG_DISPLAYS = 16
MAX_SHEET_WIDTH = 1280
MAX_SHEET_HEIGHT = 1600
MAX_SHEET_BYTES = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000
MAX_SINGLE_DISPLAY_BYTES = 30 * 1024 * 1024
_CELL_WIDTH = 620
_CELL_HEIGHT = 400
_LABEL_HEIGHT = 36


def validate_selection(catalog, selected_ids, expected_topology, current_topology):
    """Validate user selection against the exact most-recent display catalog."""
    if not isinstance(expected_topology, str) or expected_topology != current_topology:
        raise ValueError("Display topology changed; refresh the display catalog")
    if not isinstance(catalog, list) or not 1 <= len(catalog) <= MAX_CATALOG_DISPLAYS:
        raise ValueError("Invalid display catalog")
    if (not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= MAX_SELECTED_DISPLAYS
            or any(not isinstance(item, str) or not item or len(item) > 32 for item in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)):
        raise ValueError("Invalid display selection")
    by_id = {row.get("id"): row for row in catalog if isinstance(row, dict)}
    if len(by_id) != len(catalog) or any(item not in by_id for item in selected_ids):
        raise ValueError("Invalid display selection")
    return [by_id[item] for item in selected_ids]


def _enumerate_display_bounds():
    """Return native virtual-screen bounds and primary flags without device names."""
    if os.name != "nt":
        raise OSError("Display enumeration is supported only on Windows")
    from ctypes import wintypes

    class Rect(ctypes.Structure):
        _fields_ = [(name, wintypes.LONG) for name in ("left", "top", "right", "bottom")]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect), ("rcWork", Rect), ("dwFlags", wintypes.DWORD)]

    class MonitorInfoEx(ctypes.Structure):
        _fields_ = MonitorInfo._fields_ + [("szDevice", wintypes.WCHAR * 32)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_info = user32.GetMonitorInfoW
    get_info.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfoEx)]
    get_info.restype = wintypes.BOOL
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(Rect), wintypes.LPARAM
    )
    found = []

    def collect(handle, _dc, _rect, _data):
        info = MonitorInfoEx()
        info.cbSize = ctypes.sizeof(info)
        if not get_info(handle, ctypes.byref(info)):
            return True
        bounds = info.rcMonitor
        if bounds.right > bounds.left and bounds.bottom > bounds.top:
            found.append({
                "bounds": (int(bounds.left), int(bounds.top), int(bounds.right), int(bounds.bottom)),
                "primary": bool(info.dwFlags & 1),
                "device_name": str(info.szDevice),
            })
        return True

    enum_monitors = user32.EnumDisplayMonitors
    enum_monitors.argtypes = [wintypes.HDC, ctypes.POINTER(Rect), callback_type, wintypes.LPARAM]
    enum_monitors.restype = wintypes.BOOL
    callback = callback_type(collect)
    if not enum_monitors(None, None, callback, 0):
        raise OSError(ctypes.get_last_error(), "Could not enumerate Windows displays")
    return found


def _normalized_displays():
    displays = _enumerate_display_bounds()
    if not isinstance(displays, list) or not 1 <= len(displays) <= MAX_CATALOG_DISPLAYS:
        raise ValueError("Unsupported display count")
    rows = []
    for item in displays:
        bounds = item.get("bounds") if isinstance(item, dict) else None
        if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
            raise ValueError("Invalid display geometry")
        left, top, right, bottom = (int(value) for value in bounds)
        if right <= left or bottom <= top or (right - left) * (bottom - top) > MAX_SOURCE_PIXELS:
            raise ValueError("Invalid display geometry")
        row = {"bounds": (left, top, right, bottom), "primary": bool(item.get("primary"))}
        if isinstance(item.get("device_name"), str):
            row["device_name"] = item["device_name"]
        rows.append(row)
    rows.sort(key=lambda row: (not row["primary"], row["bounds"][1], row["bounds"][0], row["bounds"][3], row["bounds"][2]))
    for index, row in enumerate(rows, 1):
        left, top, right, bottom = row["bounds"]
        row.update(id=f"display-{index}", width=right - left, height=bottom - top)
    return rows


def _topology_id(rows):
    stable = [{"bounds": row["bounds"], "primary": row["primary"]} for row in rows]
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()[:16]


def get_display_catalog():
    rows = _normalized_displays()
    return {
        "topology_id": _topology_id(rows),
        "displays": [{key: row[key] for key in ("id", "width", "height", "primary")} for row in rows],
    }


def build_resolution_report(rows, backing_dimensions, *, source):
    """Return bounded logical/backing dimensions without exposing native display IDs."""
    if source not in {"windows_gdi_monitor_caps", "macos_coregraphics_pixels"}:
        raise ValueError("Invalid resolution source")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CATALOG_DISPLAYS:
        raise ValueError("Invalid display dimensions")
    if not isinstance(backing_dimensions, dict):
        raise ValueError("Invalid backing dimensions")
    displays = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid display dimensions")
        display_id = row.get("id")
        width, height = row.get("width"), row.get("height")
        backing = backing_dimensions.get(display_id)
        if (not isinstance(display_id, str) or not re.fullmatch(r"display-[1-9][0-9]?", display_id)
                or display_id in seen or type(width) is not int or type(height) is not int
                or not 1 <= width <= 32768 or not 1 <= height <= 32768 or width * height > MAX_SOURCE_PIXELS):
            raise ValueError("Invalid display dimensions")
        if (not isinstance(backing, (tuple, list)) or len(backing) != 2
                or any(type(value) is not int or not 1 <= value <= 32768 for value in backing)
                or backing[0] * backing[1] > MAX_SOURCE_PIXELS):
            raise ValueError("Invalid backing dimensions")
        seen.add(display_id)
        displays.append({
            "id": display_id, "logical_width": width, "logical_height": height,
            "backing_width": backing[0], "backing_height": backing[1],
            "scale_x": round(backing[0] / width, 4), "scale_y": round(backing[1] / height, 4),
            "primary": bool(row.get("primary")),
        })
    if set(backing_dimensions) != seen:
        raise ValueError("Backing dimensions do not match display catalog")
    return {"source": source, "displays": displays}


def _windows_backing_dimensions(device_name):
    if os.name != "nt" or not isinstance(device_name, str) or not device_name:
        raise OSError("Windows monitor resolution API is unavailable")
    from ctypes import wintypes
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    create_dc = gdi32.CreateDCW
    create_dc.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    create_dc.restype = wintypes.HDC
    get_caps = gdi32.GetDeviceCaps
    get_caps.argtypes = [wintypes.HDC, ctypes.c_int]
    get_caps.restype = ctypes.c_int
    delete_dc = gdi32.DeleteDC
    delete_dc.argtypes = [wintypes.HDC]
    delete_dc.restype = wintypes.BOOL
    dc = create_dc("DISPLAY", device_name, None, None)
    if not dc:
        raise OSError(ctypes.get_last_error(), "Could not open monitor display context")
    try:
        width, height = int(get_caps(dc, 8)), int(get_caps(dc, 10))
        if width <= 0 or height <= 0:
            raise OSError("Windows returned invalid backing resolution")
        return width, height
    finally:
        delete_dc(dc)


def get_resolution_report(display_id, expected_topology):
    rows = _normalized_displays()
    if os.name != "nt":
        raise OSError("Display resolution comparison is unsupported on this platform")
    topology_id = _topology_id(rows)
    public_catalog = [{key: row[key] for key in ("id", "width", "height", "primary")} for row in rows]
    selected = validate_selection(public_catalog, [display_id], expected_topology, topology_id)
    row = next(row for row in rows if row["id"] == selected[0]["id"])
    dimensions = {row["id"]: _windows_backing_dimensions(row.get("device_name"))}
    return {"topology_id": topology_id, **build_resolution_report([row], dimensions, source="windows_gdi_monitor_caps")}


def _capture_display(display):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=display["bounds"], all_screens=True)


def capture_contact_sheet(selected_ids, expected_topology):
    rows = _normalized_displays()
    current_topology = _topology_id(rows)
    public_catalog = [{key: row[key] for key in ("id", "width", "height", "primary")} for row in rows]
    selected = validate_selection(public_catalog, selected_ids, expected_topology, current_topology)
    if sum(int(row["width"]) * int(row["height"]) for row in selected) > MAX_SOURCE_PIXELS:
        raise ValueError("Display selection exceeds pixel limit")
    lookup = {row["id"]: row for row in rows}
    captured = []
    try:
        for entry in selected:
            row = lookup[entry["id"]]
            image = _capture_display(row)
            captured.append({**entry, "image": image})
        return build_contact_sheet(captured)
    finally:
        for item in captured:
            try:
                item["image"].close()
            except Exception:
                pass


def capture_single_display(display_id, expected_topology):
    """Capture one explicitly selected display at full resolution as a clean PNG."""
    rows = _normalized_displays()
    current_topology = _topology_id(rows)
    public_catalog = [{key: row[key] for key in ("id", "width", "height", "primary")} for row in rows]
    selected = validate_selection(public_catalog, [display_id], expected_topology, current_topology)
    display = next(row for row in rows if row["id"] == selected[0]["id"])
    if int(display["width"]) * int(display["height"]) > MAX_SOURCE_PIXELS:
        raise ValueError("Display exceeds pixel limit")
    image = _capture_display(display)
    try:
        width, height = getattr(image, "size", (0, 0))
        if not 0 < width or not 0 < height or width * height > MAX_SOURCE_PIXELS:
            raise ValueError("Captured display dimensions exceed limit")
        from PIL import Image
        output = io.BytesIO()
        with image.convert("RGB") as clean:
            clean.save(output, format="PNG", optimize=True)
        encoded = output.getvalue()
        if not encoded or len(encoded) > MAX_SINGLE_DISPLAY_BYTES:
            raise ValueError("Captured display exceeds size limit")
        manifest = {
            "id": display["id"], "width": int(width), "height": int(height),
            "primary": bool(display["primary"]),
        }
        return encoded, manifest
    finally:
        try:
            image.close()
        except Exception:
            pass


def capture_display_region(display_id, expected_topology, region):
    """Capture and encode only one owner-selected rectangle from a fresh display."""
    rows = _normalized_displays()
    current_topology = _topology_id(rows)
    public_catalog = [{key: row[key] for key in ("id", "width", "height", "primary")} for row in rows]
    selected = validate_selection(public_catalog, [display_id], expected_topology, current_topology)
    if not isinstance(region, dict) or set(region) != {"x", "y", "width", "height"}:
        raise ValueError("Invalid display region")
    x, y, width, height = (region[key] for key in ("x", "y", "width", "height"))
    if any(type(value) is not int for value in (x, y, width, height)):
        raise ValueError("Invalid display region")
    display = next(row for row in rows if row["id"] == selected[0]["id"])
    if (x < 0 or y < 0 or width < 1 or height < 1
            or x + width > display["width"] or y + height > display["height"]
            or width * height > MAX_SOURCE_PIXELS):
        raise ValueError("Display region is outside the selected display")
    if display["width"] * display["height"] > MAX_SOURCE_PIXELS:
        raise ValueError("Display exceeds pixel limit")
    source = _capture_display(display)
    cropped = None
    try:
        if getattr(source, "size", None) != (display["width"], display["height"]):
            raise ValueError("Captured display dimensions changed")
        cropped = source.crop((x, y, x + width, y + height))
        output = io.BytesIO()
        with cropped.convert("RGB") as clean:
            clean.save(output, format="PNG", optimize=True)
        encoded = output.getvalue()
        if not encoded or len(encoded) > MAX_SINGLE_DISPLAY_BYTES:
            raise ValueError("Selected display region exceeds size limit")
        return encoded, {"id": display["id"], "x": x, "y": y, "width": width, "height": height}
    finally:
        for image in (cropped, source):
            try:
                if image is not None:
                    image.close()
            except Exception:
                pass


def build_contact_sheet(selected):
    """Create one bounded JPEG from only the caller-selected display images."""
    from PIL import Image, ImageDraw, ImageOps

    if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_SELECTED_DISPLAYS:
        raise ValueError("Invalid display selection")
    seen = set()
    clean = []
    total_pixels = 0
    for item in selected:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Invalid display selection")
        display_id = item["id"]
        image = item.get("image")
        if not display_id or display_id in seen or image is None:
            raise ValueError("Invalid display selection")
        seen.add(display_id)
        width, height = getattr(image, "size", (0, 0))
        if not 0 < width or not 0 < height:
            raise ValueError("Invalid display image")
        total_pixels += width * height
        if total_pixels > MAX_SOURCE_PIXELS:
            raise ValueError("Display selection exceeds pixel limit")
        clean.append((display_id, image.copy(), bool(item.get("primary"))))

    columns = 1 if len(clean) == 1 else 2
    rows = (len(clean) + columns - 1) // columns
    sheet_width = columns * _CELL_WIDTH
    sheet_height = rows * _CELL_HEIGHT
    sheet = Image.new("RGB", (sheet_width, sheet_height), (24, 29, 38))
    draw = ImageDraw.Draw(sheet)
    manifest = []
    try:
        for index, (display_id, image, primary) in enumerate(clean):
            source_width, source_height = image.size
            image.thumbnail((_CELL_WIDTH, _CELL_HEIGHT - _LABEL_HEIGHT))
            x = (index % columns) * _CELL_WIDTH
            y = (index // columns) * _CELL_HEIGHT
            draw.text((x + 10, y + 10), f"{display_id}" + (" · primary" if primary else ""), fill=(255, 255, 255))
            left = x + (_CELL_WIDTH - image.width) // 2
            top = y + _LABEL_HEIGHT + (_CELL_HEIGHT - _LABEL_HEIGHT - image.height) // 2
            with image.convert("RGB") as rgb:
                sheet.paste(rgb, (left, top))
            manifest.append({"id": display_id, "width": source_width, "height": source_height, "primary": primary})
        if sheet.width > MAX_SHEET_WIDTH or sheet.height > MAX_SHEET_HEIGHT:
            raise ValueError("Contact sheet dimensions exceed limit")
        output = io.BytesIO()
        sheet.save(output, format="JPEG", quality=82, optimize=True)
        encoded = output.getvalue()
        if not encoded or len(encoded) > MAX_SHEET_BYTES:
            raise ValueError("Contact sheet exceeds size limit")
        return encoded, manifest
    finally:
        sheet.close()
        for _, image, _ in clean:
            image.close()
