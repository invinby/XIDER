"""Read active macOS DNS resolver addresses without exposing service labels."""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess


SOURCE = "macos_scutil_dns"
MAX_INTERFACES = 64
MAX_ADDRESSES_PER_INTERFACE = 8
_RESOLVER_START = re.compile(r"^\s*resolver\s+#\d+\s*$")
_IF_INDEX = re.compile(r"^\s*if_index\s*:\s*(\d+)(?:\s+\([^)]*\))?\s*$")
_NAMESERVER = re.compile(r"^\s*nameserver\[\d+\]\s*:\s*(\S+)\s*$")


def parse_macos_dns_settings(output: str) -> list[dict]:
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
            current_index = None
            current_addresses = []
            continue
        index_match = _IF_INDEX.match(line)
        if index_match:
            try:
                candidate = int(index_match.group(1))
            except ValueError:
                continue
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
    scutil = which("scutil") or "/usr/sbin/scutil"
    try:
        result = runner([scutil, "--dns"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"source": SOURCE, "state": "unknown", "resolvers": []}
    if result.returncode != 0:
        return {"source": SOURCE, "state": "unavailable", "resolvers": []}
    resolvers = parse_macos_dns_settings(result.stdout or "")
    return {"source": SOURCE, "state": "supported" if resolvers else "unknown", "resolvers": resolvers}
