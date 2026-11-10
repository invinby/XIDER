"""Read OS-reported negotiated network link speed (not internet throughput)."""
from __future__ import annotations

import datetime
import hashlib

SOURCE = "macos_psutil_link_speed"
_MAX_SPEED_MBPS = 1_000_000


def _interface_id(name: str) -> str:
    return hashlib.sha256(b"xider-netif-v1\0" + name.encode("utf-8", "replace")).hexdigest()[:20]


def collect_catalog(stats: object = None) -> dict:
    try:
        if stats is None:
            import psutil
            stats = psutil.net_if_stats()
        if not isinstance(stats, dict):
            raise TypeError("invalid interface status")
    except Exception:
        return {"state": "unknown", "source": SOURCE, "interfaces": [], "detail": "status_source_unavailable"}
    interfaces = [{"interface_id": _interface_id(name), "label": " ".join(name.split())[:64]}
                  for name in stats if isinstance(name, str) and name.strip()]
    interfaces.sort(key=lambda row: (row["label"].casefold(), row["interface_id"]))
    interfaces = interfaces[:32]
    return {"state": "available" if interfaces else "unsupported", "source": SOURCE,
            "interfaces": interfaces, "detail": None if interfaces else "no_interfaces"}


def collect_link_speed(interface_id: str, stats: object = None, *, checked_at: str | None = None) -> dict:
    result = {"state": "unknown", "source": SOURCE, "interface_id": interface_id if isinstance(interface_id, str) else None,
              "label": None, "speed_mbps": None, "checked_at": None, "detail": "interface_speed_unavailable"}
    if not isinstance(interface_id, str) or len(interface_id) != 20 or any(ch not in "0123456789abcdef" for ch in interface_id):
        result["detail"] = "invalid_interface"
        return result
    try:
        if stats is None:
            import psutil
            stats = psutil.net_if_stats()
        if not isinstance(stats, dict):
            raise TypeError("invalid interface status")
    except Exception:
        return result
    matches = [(name, row) for name, row in stats.items()
               if isinstance(name, str) and _interface_id(name) == interface_id]
    if len(matches) != 1:
        result["detail"] = "interface_not_found"
        return result
    name, row = matches[0]
    if getattr(row, "isup", None) is not True:
        result["state"], result["detail"] = "unsupported", "interface_down"
        return result
    speed = getattr(row, "speed", None)
    if type(speed) is not int or not 0 < speed <= _MAX_SPEED_MBPS:
        result["state"], result["detail"] = "unsupported", "link_speed_unavailable"
        return result
    if checked_at is None:
        checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not isinstance(checked_at, str):
        return result
    try:
        stamp = datetime.datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return result
    if stamp.tzinfo is None:
        return result
    normalized = stamp.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"state": "available", "source": SOURCE, "interface_id": interface_id,
            "label": " ".join(name.split())[:64], "speed_mbps": speed, "checked_at": normalized, "detail": None}
