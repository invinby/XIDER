"""Read-only lookup of the OS route selected for one literal IP address."""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from typing import Callable

MACOS_SOURCE = "macos_route_get"


def normalize_destination(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or "%" in value:
        raise ValueError("destination must be a literal IP address")
    address = ipaddress.ip_address(value)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("destination must be a unicast IP address")
    return address.compressed


def parse_macos_route(raw: str, destination: str) -> dict:
    destination = normalize_destination(destination)
    fields = {}
    for line in raw.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() in {"destination", "gateway", "interface"}:
            fields[key.strip().lower()] = value.strip()
    prefix = fields.get("destination", "")
    gateway = fields.get("gateway", "")
    interface = fields.get("interface", "")
    try:
        if "/" in prefix:
            prefix = str(ipaddress.ip_network(prefix, strict=False))
        elif prefix != "default":
            ipaddress.ip_address(prefix)
        if gateway and gateway not in {"0.0.0.0", "::"}:
            gateway = str(ipaddress.ip_address(gateway))
        else:
            gateway = "on-link"
    except ValueError as exc:
        raise ValueError("invalid route fields") from exc
    if not interface or len(interface) > 128 or any(ord(c) < 32 for c in interface):
        raise ValueError("invalid route interface")
    return {"state": "available", "destination": destination, "route_prefix": prefix,
            "gateway": gateway, "interface": interface, "metric": None}


def lookup_route(destination: str, *, platform: str | None = None,
                 runner: Callable = subprocess.run, which: Callable = shutil.which) -> dict:
    destination = normalize_destination(destination)
    if platform is None:
        import sys
        platform = "macos" if sys.platform == "darwin" else "unsupported"
    if platform != "macos":
        return {"state": "unsupported", "destination": destination}
    executable = which("/sbin/route") or "/sbin/route"
    version = ipaddress.ip_address(destination).version
    args = [executable, "-n", "get"] + (["-inet6"] if version == 6 else ["-inet"]) + [destination]
    try:
        completed = runner(args, capture_output=True, text=True, timeout=6, check=False)
        if completed.returncode != 0:
            return {"state": "unknown", "destination": destination}
        result = parse_macos_route(completed.stdout, destination)
        result["source"] = MACOS_SOURCE
        return result
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {"state": "unknown", "destination": destination}
