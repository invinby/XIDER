"""Read-only lookup of the OS route selected for one literal IP address."""
from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from typing import Callable

WINDOWS_SOURCE = "windows_find_net_route"
MACOS_SOURCE = "macos_route_get"


def normalize_destination(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or "%" in value:
        raise ValueError("destination must be a literal IP address")
    address = ipaddress.ip_address(value)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("destination must be a unicast IP address")
    return address.compressed


def _result(destination: str, prefix: str, gateway: str, interface: str, metric: object) -> dict:
    try:
        prefix_value = str(ipaddress.ip_network(prefix, strict=False)) if "/" in prefix else prefix
        if prefix_value != "default":
            if "/" in prefix_value:
                ipaddress.ip_network(prefix_value, strict=False)
            else:
                ipaddress.ip_address(prefix_value)
        gw = str(ipaddress.ip_address(gateway)) if gateway and gateway not in {"0.0.0.0", "::"} else "on-link"
    except ValueError as exc:
        raise ValueError("invalid route fields") from exc
    if not isinstance(interface, str) or not interface or len(interface) > 128 or any(ord(c) < 32 for c in interface):
        raise ValueError("invalid route interface")
    if isinstance(metric, bool) or (metric is not None and (not isinstance(metric, int) or metric < 0)):
        raise ValueError("invalid route metric")
    return {"state": "available", "destination": destination, "route_prefix": prefix_value,
            "gateway": gw, "interface": interface, "metric": metric}


def parse_windows_route(raw: str, destination: str) -> dict:
    destination = normalize_destination(destination)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("invalid route response")
    return _result(destination, str(data.get("DestinationPrefix", "")),
                   str(data.get("NextHop", "")), str(data.get("InterfaceAlias", "")),
                   data.get("RouteMetric"))


def parse_macos_route(raw: str, destination: str) -> dict:
    destination = normalize_destination(destination)
    fields = {}
    for line in raw.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() in {"destination", "gateway", "interface"}:
            fields[key.strip().lower()] = value.strip()
    return _result(destination, fields.get("destination", ""), fields.get("gateway", ""),
                   fields.get("interface", ""), None)


def lookup_route(destination: str, *, platform: str | None = None,
                 runner: Callable = subprocess.run, which: Callable = shutil.which) -> dict:
    destination = normalize_destination(destination)
    if platform is None:
        import sys
        platform = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "unsupported"
    if platform == "windows":
        executable = which("powershell.exe") or which("powershell")
        if not executable:
            return {"state": "unsupported", "destination": destination}
        script = ("$ErrorActionPreference='Stop'; $r=Find-NetRoute -RemoteIPAddress '" + destination +
                  "' | Where-Object { $_.DestinationPrefix } | Select-Object -First 1; "
                  "if(-not $r){exit 2}; [pscustomobject]@{DestinationPrefix=[string]$r.DestinationPrefix;"
                  "NextHop=[string]$r.NextHop;InterfaceAlias=[string]$r.InterfaceAlias;"
                  "InterfaceIndex=[int]$r.InterfaceIndex;RouteMetric=[int]$r.RouteMetric}|ConvertTo-Json -Compress")
        args = [executable, "-NoProfile", "-NonInteractive", "-Command", script]
        source = WINDOWS_SOURCE
    elif platform == "macos":
        executable = which("/sbin/route") or "/sbin/route"
        version = ipaddress.ip_address(destination).version
        args = [executable, "-n", "get"] + (["-inet6"] if version == 6 else ["-inet"]) + [destination]
        source = MACOS_SOURCE
    else:
        return {"state": "unsupported", "destination": destination}
    try:
        completed = runner(args, capture_output=True, text=True, timeout=6, check=False)
        if completed.returncode != 0:
            return {"state": "unknown", "destination": destination}
        parsed = parse_windows_route(completed.stdout, destination) if platform == "windows" else parse_macos_route(completed.stdout, destination)
        parsed["source"] = source
        return parsed
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
        return {"state": "unknown", "destination": destination}
