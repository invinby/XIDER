"""Read one macOS Wi-Fi RSSI sample without returning network identity."""
from __future__ import annotations

import re
import subprocess

MACOS_SOURCE = "macos_ipconfig_agrctlrssi"


def wifi_device_from_hardware_ports(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    blocks = re.split(r"(?im)^Hardware Port:\s*", text)
    for block in blocks[1:]:
        port, _, remainder = block.partition("\n")
        normalized = port.strip().casefold()
        if not any(name in normalized for name in ("wi-fi", "wifi", "airport", "беспровод")):
            continue
        match = re.search(r"(?im)^Device:\s*([A-Za-z0-9._-]{1,16})\s*$", remainder)
        if match:
            return match.group(1)
    return None


def parse_macos_summary(text: str) -> dict:
    if not isinstance(text, str):
        return {"state": "unknown", "metric": None, "value": None,
                "source": MACOS_SOURCE, "detail": "invalid_signal_sample"}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().casefold() == "agrctlrssi":
            match = re.fullmatch(r"\s*(-?\d{1,3})\s*", value)
            if not match or not -120 <= int(match.group(1)) <= 0:
                return {"state": "unknown", "metric": None, "value": None,
                        "source": MACOS_SOURCE, "detail": "invalid_signal_sample"}
            return {"state": "available", "metric": "rssi_dbm", "value": int(match.group(1)),
                    "source": MACOS_SOURCE, "detail": None}
    return {"state": "unsupported", "metric": None, "value": None,
            "source": MACOS_SOURCE, "detail": "signal_unavailable"}


def collect_wifi_quality(check_output=subprocess.check_output) -> dict:
    try:
        ports = check_output(["networksetup", "-listallhardwareports"], text=True,
                             stderr=subprocess.DEVNULL, timeout=5)
        device = wifi_device_from_hardware_ports(ports)
        if not device:
            return {"state": "unsupported", "metric": None, "value": None,
                    "source": MACOS_SOURCE, "detail": "wifi_interface_unavailable"}
        summary = check_output(["ipconfig", "getsummary", device], text=True,
                               stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return {"state": "unknown", "metric": None, "value": None,
                "source": MACOS_SOURCE, "detail": "wifi_query_failed"}
    return parse_macos_summary(summary)
