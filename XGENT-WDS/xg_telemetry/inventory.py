"""Read-only Windows identity and inventory snapshots using native APIs."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import winreg
from ctypes import wintypes

from pending_restart import parse_pending_restart_checks

log = logging.getLogger(__name__)
_PENDING_RESTART_SOURCE = "windows_pending_restart_registry"
_REBOOT_MARKERS = {
    "component_servicing": (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", None, False),
    "windows_update": (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", None, False),
    "pending_file_operations": (r"SYSTEM\CurrentControlSet\Control\Session Manager", "PendingFileRenameOperations", False),
    "update_exe_volatile": (r"SOFTWARE\Microsoft\Updates", "UpdateExeVolatile", True),
}
_DISPLAY_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"
_MAX_LABEL = 160


def _windows_only(unknown: dict) -> dict | None:
    return {**unknown, "state": "unsupported"} if os.name != "nt" else None


def _read_reg_value(root, path: str, name: str, default=None):
    with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as key:
        return winreg.QueryValueEx(key, name)[0]


def _read_pending_restart_snapshot() -> dict:
    """Check fixed reboot markers; never return raw registry values."""
    unknown = {"state": "unknown", "source": _PENDING_RESTART_SOURCE, "pending": None, "reasons": [], "completeness": "unknown"}
    unsupported = _windows_only(unknown)
    if unsupported:
        return unsupported
    checks: dict[str, bool | None] = {}
    for name, (path, value_name, nonzero) in _REBOOT_MARKERS.items():
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
                if value_name is None:
                    checks[name] = True
                else:
                    try:
                        value, _kind = winreg.QueryValueEx(key, value_name)
                    except FileNotFoundError:
                        checks[name] = False
                    else:
                        checks[name] = bool(value) and (not nonzero or int(value) != 0)
        except FileNotFoundError:
            checks[name] = False
        except OSError:
            checks[name] = None
    try:
        return parse_pending_restart_checks(json.dumps({"checks": checks}, separators=(",", ":")))
    except Exception:
        log.info("Windows pending-restart telemetry unavailable", exc_info=True)
        return unknown


def _read_os_support_identity() -> dict:
    """Read only Windows release/edition fields needed for lifecycle lookup."""
    unknown = {"state": "unknown", "family": "windows", "release": None, "edition": None}
    unsupported = _windows_only(unknown)
    if unsupported:
        return unsupported
    try:
        path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        build = _read_reg_value(winreg.HKEY_LOCAL_MACHINE, path, "CurrentBuild")
        release = _read_reg_value(winreg.HKEY_LOCAL_MACHINE, path, "DisplayVersion")
        edition_id = _read_reg_value(winreg.HKEY_LOCAL_MACHINE, path, "EditionID")
        build_number = int(build) if str(build).isdigit() else None
        version = "11" if build_number is not None and build_number >= 22000 else "10" if build_number else None
        if not version or not isinstance(release, str) or not re.fullmatch(r"\d{2}H[12]", release):
            return unknown
        edition = {"Core": "Home", "CoreSingleLanguage": "Home", "Professional": "Pro"}.get(edition_id)
        if not edition:
            return unknown
        return {"state": "supported", "family": "windows", "release": f"{version}|{release}", "edition": edition}
    except (OSError, TypeError, ValueError):
        log.info("Windows OS support identity unavailable", exc_info=True)
        return unknown


def _bounded_label(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and len(value) <= _MAX_LABEL and not any(ord(char) < 32 for char in value) else None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _physical_memory_gib() -> int | None:
    if os.name != "nt":
        return None
    try:
        function = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
        function.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
        function.restype = wintypes.BOOL
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not function(ctypes.byref(status)) or status.ullTotalPhys <= 0:
            return None
        return (status.ullTotalPhys + 2**29) // 2**30
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class _SystemInfoProcessor(ctypes.Structure):
    _fields_ = [("wProcessorArchitecture", wintypes.WORD), ("wReserved", wintypes.WORD)]


class _SystemInfoUnion(ctypes.Union):
    _anonymous_ = ("processor",)
    _fields_ = [("dwOemId", wintypes.DWORD), ("processor", _SystemInfoProcessor)]


class _SystemInfo(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("u", _SystemInfoUnion), ("dwPageSize", wintypes.DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.c_size_t),
        ("dwNumberOfProcessors", wintypes.DWORD), ("dwProcessorType", wintypes.DWORD),
        ("dwAllocationGranularity", wintypes.DWORD), ("wProcessorLevel", wintypes.WORD),
        ("wProcessorRevision", wintypes.WORD),
    ]


def _system_architecture() -> str | None:
    """Read the processor architecture using GetSystemInfo, without subprocesses."""
    if os.name != "nt":
        return None
    try:
        function = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemInfo
        function.argtypes = [ctypes.POINTER(_SystemInfo)]
        function.restype = None
        info = _SystemInfo()
        function(ctypes.byref(info))
        return {0: "x86", 9: "x64", 12: "ARM64"}.get(info.wProcessorArchitecture)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _loaded_driver_names() -> list[str] | None:
    """Enumerate loaded kernel driver module names through psapi."""
    if os.name != "nt":
        return None
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        enumerate_drivers = psapi.EnumDeviceDrivers
        enumerate_drivers.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        enumerate_drivers.restype = wintypes.BOOL
        get_base_name = psapi.GetDeviceDriverBaseNameW
        get_base_name.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
        get_base_name.restype = wintypes.DWORD

        capacity = 256
        for _attempt in range(5):
            addresses = (ctypes.c_void_p * capacity)()
            needed = wintypes.DWORD()
            ok = enumerate_drivers(addresses, ctypes.sizeof(addresses), ctypes.byref(needed))
            if not ok:
                return None
            pointer_size = ctypes.sizeof(ctypes.c_void_p)
            required = (needed.value + pointer_size - 1) // pointer_size
            if required > capacity:
                if required > 4096:
                    return None
                capacity = required
                continue
            names: set[str] = set()
            for address in addresses[:required]:
                if not address:
                    continue
                buffer = ctypes.create_unicode_buffer(_MAX_LABEL + 1)
                length = get_base_name(address, buffer, len(buffer))
                if not length or length > _MAX_LABEL:
                    continue
                name = _bounded_label(buffer.value)
                if name:
                    names.add(name)
                    if len(names) > 300:
                        return None
            return sorted(names, key=str.casefold)
        return None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _loaded_driver_rows(names: list[str], registry_rows: list[dict] | None) -> list[dict]:
    """Project loaded module names into the established driver row schema."""
    metadata = {
        str(row.get("device_name", "")).casefold(): row
        for row in (registry_rows or [])
        if isinstance(row, dict) and isinstance(row.get("device_name"), str)
    }
    rows = []
    for name in names[:300]:
        detail = metadata.get(name.casefold(), {})
        rows.append({
            "device_name": name,
            "provider": detail.get("provider"),
            "version": detail.get("version"),
            "driver_date": detail.get("driver_date"),
        })
    return rows


def _display_names() -> list[str]:
    names: set[str] = set()
    base = r"SYSTEM\CurrentControlSet\Enum"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ) as root:
            for index in range(256):
                try:
                    enumerator = winreg.EnumKey(root, index)
                except OSError:
                    break
                enum_path = base + "\\" + enumerator
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, enum_path, 0, winreg.KEY_READ) as enum_key:
                    for device_index in range(4096):
                        try:
                            device = winreg.EnumKey(enum_key, device_index)
                        except OSError:
                            break
                        device_path = enum_path + "\\" + device
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, device_path, 0, winreg.KEY_READ) as device_key:
                            try:
                                class_guid, _kind = winreg.QueryValueEx(device_key, "ClassGUID")
                            except OSError:
                                continue
                            if not isinstance(class_guid, str) or class_guid.lower() != _DISPLAY_CLASS_GUID:
                                continue
                            try:
                                label, _kind = winreg.QueryValueEx(device_key, "FriendlyName")
                            except OSError:
                                try:
                                    label, _kind = winreg.QueryValueEx(device_key, "DeviceDesc")
                                except OSError:
                                    continue
                            label = _bounded_label(label)
                            if label:
                                names.add(label)
                                if len(names) >= 8:
                                    return sorted(names)
    except OSError:
        pass
    return sorted(names)


def _read_hardware_inventory_snapshot() -> dict:
    """Project bounded, non-serial hardware model fields from native sources."""
    unknown = {"state": "unknown", "manufacturer": None, "model": None, "cpu": None, "architecture": None, "memory_gib": None, "graphics": []}
    unsupported = _windows_only(unknown)
    if unsupported:
        return unsupported
    try:
        bios = r"HARDWARE\DESCRIPTION\System\BIOS"
        cpu_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        manufacturer = _bounded_label(_read_reg_value(winreg.HKEY_LOCAL_MACHINE, bios, "SystemManufacturer"))
        model = _bounded_label(_read_reg_value(winreg.HKEY_LOCAL_MACHINE, bios, "SystemProductName"))
        cpu = _bounded_label(_read_reg_value(winreg.HKEY_LOCAL_MACHINE, cpu_path, "ProcessorNameString"))
        architecture = _system_architecture()
        graphics = _display_names()
        result = {"state": "supported", "manufacturer": manufacturer, "model": model, "cpu": cpu,
                  "architecture": architecture, "memory_gib": _physical_memory_gib(), "graphics": graphics}
        if not any(value is not None for key, value in result.items() if key not in {"state", "graphics"}) and not graphics:
            return unknown
        return result
    except (OSError, TypeError, ValueError):
        log.info("Windows hardware inventory unavailable", exc_info=True)
        return unknown


def _driver_rows() -> list[dict] | None:
    """Read bounded driver labels from Enum and class registry keys."""
    enum_base = r"SYSTEM\CurrentControlSet\Enum"
    class_base = r"SYSTEM\CurrentControlSet\Control\Class"
    rows: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, enum_base, 0, winreg.KEY_READ) as root:
            for enum_index in range(256):
                try:
                    enumerator = winreg.EnumKey(root, enum_index)
                except OSError:
                    break
                enum_path = enum_base + "\\" + enumerator
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, enum_path, 0, winreg.KEY_READ) as enum_key:
                    for device_index in range(4096):
                        try:
                            device = winreg.EnumKey(enum_key, device_index)
                        except OSError:
                            break
                        device_path = enum_path + "\\" + device
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, device_path, 0, winreg.KEY_READ) as device_key:
                            try:
                                driver_ref, _kind = winreg.QueryValueEx(device_key, "Driver")
                                device_name, _kind = winreg.QueryValueEx(device_key, "FriendlyName")
                            except OSError:
                                try:
                                    driver_ref, _kind = winreg.QueryValueEx(device_key, "Driver")
                                    device_name, _kind = winreg.QueryValueEx(device_key, "DeviceDesc")
                                except OSError:
                                    continue
                        if not isinstance(driver_ref, str) or not re.fullmatch(r"\{[0-9a-fA-F-]{36}\}\\\d{4}", driver_ref):
                            continue
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_base + "\\" + driver_ref, 0, winreg.KEY_READ) as driver_key:
                                provider = winreg.QueryValueEx(driver_key, "ProviderName")[0]
                                version = winreg.QueryValueEx(driver_key, "DriverVersion")[0]
                                driver_date = winreg.QueryValueEx(driver_key, "DriverDate")[0]
                        except OSError:
                            continue
                        item = {
                            "device_name": _bounded_label(device_name),
                            "provider": _bounded_label(provider),
                            "version": _bounded_label(version),
                            "driver_date": _bounded_label(driver_date),
                        }
                        if not item["device_name"]:
                            continue
                        rows.append(item)
                        if len(rows) > 300:
                            return None
    except OSError:
        return None
    return rows


def _read_driver_inventory_snapshot() -> dict:
    """Read at most 300 driver labels; omit hardware identifiers and paths."""
    unknown = {"state": "unknown", "drivers": []}
    unsupported = _windows_only(unknown)
    if unsupported:
        return unsupported
    try:
        loaded_names = _loaded_driver_names()
        registry_rows = _driver_rows()
        if loaded_names:
            return {"state": "supported", "drivers": _loaded_driver_rows(loaded_names, registry_rows)}
        if registry_rows is None:
            return unknown
        return {"state": "supported", "drivers": registry_rows}
    except (OSError, TypeError, ValueError):
        log.info("Windows driver inventory unavailable", exc_info=True)
        return unknown
