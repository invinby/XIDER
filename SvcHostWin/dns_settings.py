"""Read the active Windows DNS resolver addresses without exposing suffixes."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import re


SOURCE = "windows_get_dns_client_server_address"
MAX_INTERFACES = 64
MAX_ADDRESSES_PER_INTERFACE = 8
_RESOLVER_START = re.compile(r"^\s*resolver\s+#\d+\s*$")
_IF_INDEX = re.compile(r"^\s*if_index\s*:\s*(\d+)(?:\s+\([^)]*\))?\s*$")
_NAMESERVER = re.compile(r"^\s*nameserver\[\d+\]\s*:\s*(\S+)\s*$")


def _index(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 2**31 - 1 else None


def parse_windows_dns_settings(output: str) -> list[dict]:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    grouped: dict[int, list[str]] = {}
    for row in payload[:MAX_INTERFACES * 2]:
        if not isinstance(row, dict):
            continue
        index = _index(row.get("InterfaceIndex"))
        values = row.get("ServerAddresses")
        if index is None or not isinstance(values, list):
            continue
        addresses = grouped.setdefault(index, [])
        for value in values[:MAX_ADDRESSES_PER_INTERFACE]:
            if not isinstance(value, str):
                continue
            try:
                address = str(ipaddress.ip_address(value.strip()))
            except ValueError:
                continue
            if address not in addresses and len(addresses) < MAX_ADDRESSES_PER_INTERFACE:
                addresses.append(address)
    return [
        {"interface_index": index, "addresses": addresses}
        for index, addresses in sorted(grouped.items()) if addresses
    ][:MAX_INTERFACES]


def parse_macos_dns_settings(output: str) -> list[dict]:
    """Parser shared by fixture tests; scutil records only interface IDs/IPs."""
    grouped: dict[int, list[str]] = {}
    current_index = None
    current_addresses: list[str] = []

    def flush():
        if current_index is not None and current_addresses:
            bucket = grouped.setdefault(current_index, [])
            for address in current_addresses:
                if address not in bucket and len(bucket) < MAX_ADDRESSES_PER_INTERFACE:
                    bucket.append(address)

    for line in (output or "").splitlines()[:10000]:
        if _RESOLVER_START.match(line):
            flush()
            current_index, current_addresses = None, []
            continue
        index_match = _IF_INDEX.match(line)
        if index_match:
            candidate = int(index_match.group(1))
            if 1 <= candidate <= 2**31 - 1:
                current_index = candidate
            continue
        address_match = _NAMESERVER.match(line)
        if address_match:
            try:
                address = str(ipaddress.ip_address(address_match.group(1)))
            except ValueError:
                continue
            if address not in current_addresses and len(current_addresses) < MAX_ADDRESSES_PER_INTERFACE:
                current_addresses.append(address)
    flush()
    return [
        {"interface_index": index, "addresses": addresses}
        for index, addresses in sorted(grouped.items()) if addresses
    ][:MAX_INTERFACES]


def read_dns_settings(*, runner=subprocess.run, which=shutil.which) -> dict:
    powershell = which("powershell.exe") or which("powershell")
    if not powershell:
        return {"source": SOURCE, "state": "unsupported", "resolvers": []}
    script = (
        "$ProgressPreference='SilentlyContinue'; "
        "Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses | "
        "ConvertTo-Json -Compress -Depth 3"
    )
    try:
        result = runner(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"source": SOURCE, "state": "unknown", "resolvers": []}
    if result.returncode != 0:
        return {"source": SOURCE, "state": "unavailable", "resolvers": []}
    resolvers = parse_windows_dns_settings(result.stdout or "")
    return {"source": SOURCE, "state": "supported" if resolvers else "unknown", "resolvers": resolvers}
