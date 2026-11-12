"""Best-effort JSON cache for slow, read-only inventory snapshots."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_VERSION = 1


def read_inventory_snapshot(
    name: str,
    reader: Callable[[], dict],
    config_dir: Path,
    *,
    force: bool = False,
    unknown: dict | None = None,
    now: float | None = None,
) -> dict:
    """Return a fresh cached supported snapshot, otherwise collect and cache it.

    Cache I/O and collector failures are deliberately non-fatal. The caller
    supplies the existing command's unknown shape so its response contract is
    preserved if collection itself fails.
    """
    fallback = copy.deepcopy(unknown or {"state": "unknown"})
    if not re.fullmatch(r"[a-z0-9-]{1,48}", name):
        log.warning("Invalid inventory cache name")
        return fallback
    path = Path(config_dir) / "inventory" / f"{name}.json"
    current = time.time() if now is None else now
    if not force:
        try:
            with path.open("r", encoding="utf-8") as stream:
                envelope = json.load(stream)
            cached_at = envelope.get("cached_at")
            value = envelope.get("value")
            age = current - cached_at if isinstance(cached_at, (int, float)) else None
            if (
                envelope.get("version") == CACHE_VERSION
                and age is not None and 0 <= age < CACHE_TTL_SECONDS
                and isinstance(value, dict) and value.get("state") == "supported"
            ):
                return value
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    try:
        value = reader()
        if not isinstance(value, dict):
            log.warning("Inventory reader returned a non-object snapshot: %s", name)
            return fallback
    except Exception:
        log.info("Inventory snapshot collection failed: %s", name, exc_info=True)
        return fallback

    if value.get("state") == "supported":
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix=f".{name}-", suffix=".tmp", dir=path.parent)
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"version": CACHE_VERSION, "cached_at": current, "value": value}, stream,
                          ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            log.info("Inventory cache write failed: %s", name, exc_info=True)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return value
