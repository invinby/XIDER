"""Owner-requested per-interface byte counters; no polling or addresses."""
from __future__ import annotations

import datetime
import hashlib
import re

SOURCE = "macos_psutil_interface_bytes"
_ID_RE = re.compile(r"^[0-9a-f]{20}$")
_MAX_INTERFACES = 32
_MAX_LABEL = 64
_MAX_COUNTER = (1 << 64) - 1


def _clean_label(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
    return cleaned[:_MAX_LABEL]


def _interface_id(name: str) -> str:
    return hashlib.sha256(b"xider-netif-v1\0" + name.encode("utf-8", "replace")).hexdigest()[:20]


def interface_catalog(names) -> list[dict]:
    if not isinstance(names, (list, tuple, set)):
        return []
    rows = []
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        label = _clean_label(name)
        interface_id = _interface_id(name)
        if not label or interface_id in seen:
            continue
        seen.add(interface_id)
        rows.append({"interface_id": interface_id, "label": label})
    rows.sort(key=lambda row: (row["label"].casefold(), row["interface_id"]))
    return rows[:_MAX_INTERFACES]


def _valid_counter(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_COUNTER


def sample_interface(interface_id: object, counters: object, *, sampled_at=None) -> dict:
    base = {"state": "unknown", "source": SOURCE, "interface_id": interface_id if isinstance(interface_id, str) else None,
            "label": None, "sent_bytes": None, "received_bytes": None, "sampled_at": None, "detail": "counter_unavailable"}
    if not isinstance(interface_id, str) or not _ID_RE.fullmatch(interface_id) or not isinstance(counters, dict):
        return base
    matches = [(name, row) for name, row in counters.items()
               if isinstance(name, str) and _interface_id(name) == interface_id]
    if len(matches) != 1:
        base["detail"] = "interface_not_found"
        return base
    name, row = matches[0]
    sent = getattr(row, "bytes_sent", None)
    received = getattr(row, "bytes_recv", None)
    if not _valid_counter(sent) or not _valid_counter(received):
        return base
    stamp = sampled_at or datetime.datetime.now(datetime.timezone.utc)
    if not isinstance(stamp, datetime.datetime) or stamp.tzinfo is None:
        return base
    return {
        "state": "available", "source": SOURCE, "interface_id": interface_id,
        "label": _clean_label(name), "sent_bytes": sent, "received_bytes": received,
        "sampled_at": stamp.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "detail": None,
    }


def collect_catalog() -> dict:
    try:
        import psutil

        counters = psutil.net_io_counters(pernic=True)
        if not isinstance(counters, dict):
            raise TypeError("invalid counters")
        rows = interface_catalog(list(counters))
    except Exception:
        return {"state": "unknown", "source": SOURCE, "interfaces": [], "detail": "counter_source_unavailable"}
    if not rows:
        return {"state": "unsupported", "source": SOURCE, "interfaces": [], "detail": "no_interfaces"}
    return {"state": "available", "source": SOURCE, "interfaces": rows, "detail": None}


def collect_interface_traffic(interface_id: str) -> dict:
    try:
        import psutil

        counters = psutil.net_io_counters(pernic=True)
    except Exception:
        return {"state": "unknown", "source": SOURCE, "interface_id": interface_id,
                "label": None, "sent_bytes": None, "received_bytes": None, "sampled_at": None,
                "detail": "counter_source_unavailable"}
    return sample_interface(interface_id, counters)
