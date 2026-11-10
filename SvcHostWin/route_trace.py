"""Bounded numeric-only route tracing for a single owner-selected IP."""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from typing import Callable

MAX_HOPS = 12
WINDOWS_SOURCE = "windows_tracert_icmp"
MACOS_SOURCE = "macos_traceroute_udp"
_IP_TOKEN = re.compile(r"(?<![0-9A-Fa-f:.])(?:\[[0-9A-Fa-f:.%]+\]|[0-9A-Fa-f:.%]+)(?![0-9A-Fa-f:.])")


def normalize_target(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 64 or "%" in value:
        raise ValueError("target must be a literal unicast IP")
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("target must be a literal unicast IP") from exc
    if parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
        raise ValueError("target must be a literal unicast IP")
    return str(parsed)


def _line_address(line: str, family: int) -> str | None:
    for token in _IP_TOKEN.findall(line[:512]):
        token = token.strip("[]")
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if address.version == family and not address.is_unspecified and not address.is_multicast:
            return str(address)
    return None


def parse_trace_output(output: object, target: str, *, max_hops: int = MAX_HOPS) -> dict:
    normalized = normalize_target(target)
    if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 1 <= max_hops <= MAX_HOPS:
        raise ValueError("max_hops out of range")
    if not isinstance(output, str):
        output = ""
    rows = {}
    for line in output[:16_384].splitlines():
        match = re.match(r"\s*(\d{1,2})\s+(.{0,512})$", line)
        if not match:
            continue
        index = int(match.group(1))
        if not 1 <= index <= max_hops or index in rows:
            continue
        rows[index] = _line_address(match.group(2), ipaddress.ip_address(normalized).version)
    hops = [{"index": index, "address": rows.get(index)} for index in sorted(rows)]
    reached = any(hop["address"] == normalized for hop in hops)
    state = "complete" if reached else "partial" if hops else "unknown"
    return {"state": state, "target": normalized, "reached": reached, "hops": hops}


def trace_route(target: object, *, platform: str | None = None,
                runner: Callable = subprocess.run, which: Callable = shutil.which) -> dict:
    normalized = normalize_target(target)
    selected_platform = platform or ("windows" if os.name == "nt" else "macos")
    family = ipaddress.ip_address(normalized).version
    if selected_platform == "windows":
        executable = which("tracert.exe") or which("tracert")
        if not executable:
            return {"state": "unsupported", "target": normalized, "reached": False, "hops": [],
                    "source": WINDOWS_SOURCE, "detail": "tracert_unavailable"}
        args = [executable, "-d", "-h", str(MAX_HOPS), "-w", "250", "-4" if family == 4 else "-6", normalized]
        source = WINDOWS_SOURCE
        timeout = 12
    elif selected_platform == "macos":
        executable = which("traceroute") or ("/sbin/traceroute" if os.path.exists("/sbin/traceroute") else None)
        if not executable:
            return {"state": "unsupported", "target": normalized, "reached": False, "hops": [],
                    "source": MACOS_SOURCE, "detail": "traceroute_unavailable"}
        args = [executable, "-n", "-q", "1", "-m", str(MAX_HOPS), "-w", "1", normalized]
        source = MACOS_SOURCE
        timeout = 16
    else:
        raise ValueError("unsupported platform")
    try:
        completed = runner(args, capture_output=True, text=True, timeout=timeout, check=False)
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        parsed = parse_trace_output(output, normalized)
    except subprocess.TimeoutExpired as exc:
        captured = exc.stdout or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", "replace")
        parsed = parse_trace_output(str(captured), normalized)
        if parsed["state"] == "unknown":
            parsed["state"] = "unknown"
    except OSError:
        parsed = {"state": "unknown", "target": normalized, "reached": False, "hops": []}
    return {**parsed, "source": source, "detail": None}
