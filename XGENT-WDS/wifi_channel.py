"""Read the active WLAN channel metadata without returning network identity."""
from __future__ import annotations

import re
import subprocess

WINDOWS_SOURCE = "windows_netsh_wlan_channel"
_WIDTHS = {20, 40, 80, 160, 320}


def parse_windows_channel(text: str) -> dict:
    if not isinstance(text, str):
        return {"state": "unknown", "channel": None, "width_mhz": None,
                "width_state": "unknown", "source": WINDOWS_SOURCE, "detail": "invalid_channel_sample"}
    channel = width = None
    saw_channel = False
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        name = key.strip().casefold()
        if name in {"channel", "канал"}:
            saw_channel = True
            match = re.fullmatch(r"\s*(\d{1,3})\s*", raw)
            if not match or not 1 <= int(match.group(1)) <= 233:
                return {"state": "unknown", "channel": None, "width_mhz": None,
                        "width_state": "unknown", "source": WINDOWS_SOURCE, "detail": "invalid_channel_sample"}
            channel = int(match.group(1))
        elif name in {"channel width", "ширина канала"}:
            match = re.fullmatch(r"\s*(\d{1,3})\s*(?:mhz|мгц)\s*", raw, re.IGNORECASE)
            if match and int(match.group(1)) in _WIDTHS:
                width = int(match.group(1))
    if not saw_channel or channel is None:
        return {"state": "unsupported", "channel": None, "width_mhz": None,
                "width_state": "unsupported", "source": WINDOWS_SOURCE, "detail": "channel_unavailable"}
    return {"state": "available", "channel": channel, "width_mhz": width,
            "width_state": "available" if width is not None else "unsupported",
            "source": WINDOWS_SOURCE, "detail": None}


def collect_wifi_channel(check_output=subprocess.check_output) -> dict:
    try:
        output = check_output(["netsh", "wlan", "show", "interfaces"], text=True,
                              stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return {"state": "unknown", "channel": None, "width_mhz": None,
                "width_state": "unknown", "source": WINDOWS_SOURCE, "detail": "wifi_query_failed"}
    return parse_windows_channel(output)
