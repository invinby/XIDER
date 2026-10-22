"""Bounded one-shot reachability check for the active IPv4 default gateway."""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import sys
import threading


SOURCE = "active_ipv4_default_route_and_single_icmp_echo"
_PROBE_SLOT = threading.BoundedSemaphore(1)
_MAC_GATEWAY = re.compile(r"^\s*gateway:\s*(\S+)\s*$", re.IGNORECASE)


def _ipv4(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if address.version != 4 or address.is_unspecified or address.is_multicast or address.is_loopback:
        return None
    return str(address)


def parse_windows_gateway(output: object) -> str | None:
    """Parse the single IPv4 NextHop emitted by the fixed PowerShell query."""
    if not isinstance(output, str) or len(output) > 4096:
        return None
    for line in output.splitlines()[:16]:
        candidate = _ipv4(line)
        if candidate:
            return candidate
    return None


def parse_macos_gateway(output: object) -> str | None:
    """Parse only the gateway field from `route -n get -inet default`."""
    if not isinstance(output, str) or len(output) > 4096:
        return None
    for line in output.splitlines()[:64]:
        match = _MAC_GATEWAY.fullmatch(line)
        if match:
            return _ipv4(match.group(1))
    return None


def _unknown(detail: str, gateway: str | None = None) -> dict:
    return {"gateway": gateway, "state": "unknown", "source": SOURCE, "detail": detail}


def probe_gateway(*, platform: str | None = None, runner=subprocess.run, which=shutil.which) -> dict:
    """Resolve the default IPv4 gateway, then send one bounded ICMP echo only."""
    selected = platform or sys.platform
    if selected in {"win32", "windows"}:
        powershell = which("powershell.exe") or which("powershell")
        if not powershell:
            return {"gateway": None, "state": "unsupported", "source": SOURCE, "detail": "powershell_unavailable"}
        command = [
            powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            "Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -PolicyStore ActiveStore "
            "| Sort-Object RouteMetric | Select-Object -First 1 -ExpandProperty NextHop",
        ]
        parser = parse_windows_gateway
        ping_name = which("ping.exe") or which("ping")
    elif selected in {"darwin", "macos"}:
        route = which("route") or "/sbin/route"
        command = [route, "-n", "get", "-inet", "default"]
        parser = parse_macos_gateway
        ping_name = which("ping") or "/sbin/ping"
    else:
        return {"gateway": None, "state": "unsupported", "source": SOURCE, "detail": "platform_unsupported"}

    if not _PROBE_SLOT.acquire(blocking=False):
        return _unknown("gateway_probe_busy")
    try:
        try:
            route_result = runner(command, capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return _unknown("default_route_lookup_failed")
        if getattr(route_result, "returncode", 1) != 0:
            return _unknown("default_route_lookup_failed")
        gateway = parser(getattr(route_result, "stdout", ""))
        if gateway is None:
            return {"gateway": None, "state": "no_gateway", "source": SOURCE, "detail": None}
        if not ping_name:
            return _unknown("icmp_probe_unavailable", gateway)

        ping_args = ([ping_name, "-n", "1", "-w", "1000", gateway]
                     if selected in {"win32", "windows"}
                     else [ping_name, "-n", "-c", "1", "-W", "1000", gateway])
        try:
            ping_result = runner(ping_args, capture_output=True, text=True, timeout=3, check=False)
        except subprocess.TimeoutExpired:
            return _unknown("icmp_probe_timeout", gateway)
        except OSError:
            return _unknown("icmp_probe_unavailable", gateway)
        if getattr(ping_result, "returncode", 1) == 0:
            return {"gateway": gateway, "state": "reachable", "source": SOURCE, "detail": None}
        # ICMP can be filtered by a functioning router; nonzero is not proof of outage.
        return _unknown("gateway_did_not_answer_icmp", gateway)
    finally:
        _PROBE_SLOT.release()
