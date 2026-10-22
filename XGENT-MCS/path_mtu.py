"""Bounded IPv4 path-MTU probes using one non-fragmenting ICMP request at a time."""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from typing import Callable

MIN_PAYLOAD = 548
MAX_PAYLOAD = 1472
WINDOWS_SOURCE = "windows_ping_ipv4_df"
MACOS_SOURCE = "macos_ping_ipv4_df"
_TOO_LARGE = re.compile(
    r"fragment(?:ed|ation)|message too long|packet too big|packet is too large|"
    r"need(?:s)? to be fragmented|mtu exceeded|пакет.{0,40}(?:фрагмент|слишком большой)|"
    r"(?:фрагмент|mtu).{0,40}(?:требуется|нужен|превыш)", re.IGNORECASE,
)


def normalize_target(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 64:
        raise ValueError("target must be an IPv4 unicast literal")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("target must be an IPv4 unicast literal") from exc
    if address.version != 4 or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError("target must be an IPv4 unicast literal")
    return str(address)


def probe_ipv4_mtu(target: object, payload_size: int, *, platform: str | None = None,
                   runner: Callable = subprocess.run, which: Callable = shutil.which) -> str:
    normalized = normalize_target(target)
    if type(payload_size) is not int or not MIN_PAYLOAD <= payload_size <= MAX_PAYLOAD:
        raise ValueError("payload size out of range")
    selected = platform or ("windows" if os.name == "nt" else "macos")
    if selected == "windows":
        executable = which("ping.exe") or which("ping")
        if not executable:
            return "unsupported"
        args = [executable, "/n", "1", "/w", "800", "/f", "/l", str(payload_size), "/4", normalized]
    elif selected == "macos":
        executable = which("ping") or ("/sbin/ping" if os.path.exists("/sbin/ping") else None)
        if not executable:
            return "unsupported"
        args = [executable, "-D", "-c", "1", "-W", "800", "-s", str(payload_size), "-n", normalized]
    else:
        raise ValueError("unsupported platform")
    try:
        result = runner(args, capture_output=True, text=True, timeout=1.2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode == 0:
        return "reachable"
    if _TOO_LARGE.search(output[:8192]):
        return "too_large"
    return "unknown"


def search_path_mtu(probe: Callable[[int], str]) -> dict:
    first = probe(MIN_PAYLOAD)
    if first == "unsupported":
        return {"state": "unsupported", "mtu": None, "max_tested": False}
    if first != "reachable":
        return {"state": "unknown", "mtu": None, "max_tested": False}
    low, high = MIN_PAYLOAD, MAX_PAYLOAD
    largest = probe(high)
    if largest == "unsupported":
        return {"state": "unsupported", "mtu": None, "max_tested": False}
    if largest == "reachable":
        return {"state": "supported", "mtu": 1500, "max_tested": True}
    if largest != "too_large":
        return {"state": "unknown", "mtu": None, "max_tested": False}
    while high - low > 1:
        middle = (low + high) // 2
        outcome = probe(middle)
        if outcome == "reachable":
            low = middle
        elif outcome == "too_large":
            high = middle
        else:
            return {"state": "unknown", "mtu": None, "max_tested": False}
    return {"state": "supported", "mtu": low + 28, "max_tested": False}


def diagnose_path_mtu(target: object, *, platform: str | None = None) -> dict:
    normalized = normalize_target(target)
    selected = platform or ("windows" if os.name == "nt" else "macos")
    source = WINDOWS_SOURCE if selected == "windows" else MACOS_SOURCE
    result = search_path_mtu(lambda size: probe_ipv4_mtu(normalized, size, platform=selected))
    return {**result, "target": normalized, "source": source, "detail": None}
