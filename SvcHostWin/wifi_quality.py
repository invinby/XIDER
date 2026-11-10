"""Read one Windows WLAN signal sample without returning network identity."""
from __future__ import annotations

import re
import subprocess

WINDOWS_SOURCE = "windows_netsh_signal_percent"


def parse_windows_signal(text: str) -> dict:
    if not isinstance(text, str):
        return {"state": "unknown", "metric": None, "value": None,
                "source": WINDOWS_SOURCE, "detail": "invalid_signal_sample"}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().casefold() in {"signal", "сигнал"}:
            match = re.fullmatch(r"\s*(\d{1,3})\s*%\s*", value)
            if not match or not 0 <= int(match.group(1)) <= 100:
                return {"state": "unknown", "metric": None, "value": None,
                        "source": WINDOWS_SOURCE, "detail": "invalid_signal_sample"}
            return {"state": "available", "metric": "signal_percent", "value": int(match.group(1)),
                    "source": WINDOWS_SOURCE, "detail": None}
    return {"state": "unsupported", "metric": None, "value": None,
            "source": WINDOWS_SOURCE, "detail": "signal_unavailable"}


def collect_wifi_quality(check_output=subprocess.check_output) -> dict:
    try:
        output = check_output(["netsh", "wlan", "show", "interfaces"], text=True,
                              stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return {"state": "unknown", "metric": None, "value": None,
                "source": WINDOWS_SOURCE, "detail": "wifi_query_failed"}
    return parse_windows_signal(output)
