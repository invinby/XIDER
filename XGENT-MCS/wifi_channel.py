"""Read active Wi-Fi channel metadata from wdutil without returning SSID."""
from __future__ import annotations

import re
import subprocess

MACOS_SOURCE = "macos_wdutil_channel"
_WIDTHS = {20, 40, 80, 160, 320}


def parse_macos_channel(text: str) -> dict:
    if not isinstance(text, str):
        return {"state": "unknown", "channel": None, "width_mhz": None,
                "width_state": "unknown", "source": MACOS_SOURCE, "detail": "invalid_channel_sample"}
    channel = width = None
    saw_channel = False
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator or key.strip().casefold() != "channel":
            continue
        saw_channel = True
        match = re.match(r"\s*(\d{1,3})(.*)$", raw)
        if not match or not 1 <= int(match.group(1)) <= 233:
            return {"state": "unknown", "channel": None, "width_mhz": None,
                    "width_state": "unknown", "source": MACOS_SOURCE, "detail": "invalid_channel_sample"}
        channel = int(match.group(1))
        width_match = re.search(r"\b(20|40|80|160|320)\s*MHz\b", match.group(2), re.IGNORECASE)
        if width_match:
            width = int(width_match.group(1))
    if not saw_channel or channel is None:
        return {"state": "unsupported", "channel": None, "width_mhz": None,
                "width_state": "unsupported", "source": MACOS_SOURCE, "detail": "channel_unavailable"}
    return {"state": "available", "channel": channel, "width_mhz": width,
            "width_state": "available" if width is not None else "unsupported",
            "source": MACOS_SOURCE, "detail": None}


def collect_wifi_channel(check_output=subprocess.check_output) -> dict:
    try:
        output = check_output(["wdutil", "info"], text=True,
                              stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return {"state": "unknown", "channel": None, "width_mhz": None,
                "width_state": "unknown", "source": MACOS_SOURCE, "detail": "wifi_query_failed"}
    return parse_macos_channel(output)
