"""Windows display-orientation operations used by the agent UI commands."""

from __future__ import annotations

import ctypes


class _POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _DEVMODEW(ctypes.Structure):
    """Minimal DEVMODE layout for display orientation (WinAPI wingdi.h)."""
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPosition", _POINTL),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("dmICMMethod", ctypes.c_ulong),
        ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong),
        ("dmDitherType", ctypes.c_ulong),
        ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong),
        ("dmPanningWidth", ctypes.c_ulong),
        ("dmPanningHeight", ctypes.c_ulong),
    ]


DM_DISPLAYORIENTATION = 0x00000080
CDS_RESET = 0x00000004
DISP_CHANGE_SUCCESSFUL = 0


def rotate_screen(angle: int, *, orientation_value) -> tuple[bool, str]:
    """Rotate the primary display through ChangeDisplaySettingsEx."""
    orient = orientation_value(angle)
    if orient is None:
        return False, f"Некорректный угол: {angle!r} (нужно 0/90/180/270)"
    try:
        user32 = ctypes.windll.user32
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return False, "EnumDisplaySettings не удался"

        is_current_portrait = dm.dmDisplayOrientation in (1, 3)
        is_target_portrait = orient in (1, 3)
        if is_current_portrait != is_target_portrait:
            dm.dmPelsWidth, dm.dmPelsHeight = dm.dmPelsHeight, dm.dmPelsWidth
            dm.dmFields = DM_DISPLAYORIENTATION | 0x00080000 | 0x00100000
        else:
            dm.dmFields = DM_DISPLAYORIENTATION

        dm.dmDisplayOrientation = orient
        result = user32.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None, CDS_RESET, None)
        if result != DISP_CHANGE_SUCCESSFUL:
            return False, f"ChangeDisplaySettingsEx вернул {result}"
        return True, f"Экран повёрнут на {angle}°"
    except Exception as exc:  # noqa: BLE001
        return False, f"Ошибка поворота экрана: {exc}"


def read_current_display_angle() -> int | None:
    """Return the current primary-display orientation in degrees, if available."""
    try:
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return None
        return {0: 0, 1: 90, 2: 180, 3: 270}.get(int(dm.dmDisplayOrientation))
    except Exception:
        return None
