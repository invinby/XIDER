"""Side-effect-free formatting and parsing helpers used by the Windows agent."""

from __future__ import annotations

import os
import re
import socket
import time
from collections.abc import Iterable, Mapping
from typing import Any

import psutil


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
    """Приводит psutil.sbattery (или None) к словарю для MQTT."""
    if battery is None:
        return {"available": False}
    secsleft = battery.secsleft
    if secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        secsleft = None
    plugged = bool(battery.power_plugged)
    return {
        "available": True,
        "percent": round(float(battery.percent), 1),
        "source": "psutil.sensors_battery",
        "sampled_at": int(time.time()),
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
        for addr in nic_addrs:
            if addr.family == socket.AF_INET:
                ipv4 = addr.address
            elif addr.family == socket.AF_INET6:
                ipv6 = addr.address.split("%")[0]
            elif addr.family == psutil.AF_LINK:
                mac = addr.address
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
    """Сводка по службам Windows: счётчики и auto-start, которые не запущены."""
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
    """Топ-N процессов по памяти с совместимыми `lines` и `top`."""
    items = []
    for proc in procs:
        try:
            info = proc.info
        except Exception:
            continue
        name = info.get("name")
        mem = info.get("memory_percent") or 0
        items.append((name, mem, info.get("pid")))
    items.sort(key=lambda item: (item[1] or 0), reverse=True)
    top = items[:top_n]
    lines = [f"{name}: {mem:.1f}%" for name, mem, _ in top if name is not None]
    structured = [
        {"name": name, "pid": pid, "mem_percent": round(mem, 1)}
        for name, mem, pid in top if name is not None
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
    """Список дисков с размерами."""
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


def map_hotkeys(spec: str) -> list[int]:
    """Разобрать 'ctrl+shift+esc' → список VK-кодов (для keybd_event)."""
    vk_map = {
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
        if raw in vk_map:
            keys.append(vk_map[raw])
        elif len(raw) == 1 and raw.isalnum():
            keys.append(ord(raw.upper()))
        elif re.fullmatch(r"f[1-9]|f1[0-2]", raw):
            keys.append(0x70 + int(raw[1:]) - 1)
        else:
            raise ValueError(f"unknown key: {raw}")
    return keys


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
    safe_keys = (
        "PATH", "USERNAME", "USER", "HOME", "USERPROFILE", "COMPUTERNAME",
        "HOSTNAME", "OS", "TEMP", "TMP", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "LANG", "SHELL", "PYTHON_HOME",
    )
    wanted = [name.upper() for name in (names or safe_keys)]
    result = {}
    for key, value in os.environ.items():
        if key.upper() in wanted:
            result[key] = value if len(value) < 400 else value[:397] + "..."
    return result


def _clamp(value, lo, hi, default):
    """Привести value к int в [lo..hi]; при ошибке — default."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


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
