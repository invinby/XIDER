"""Bounded, local-only aggregation for Windows SRUM Energy Estimation CSV."""

from __future__ import annotations

import csv
import io
import math
import ntpath
import re
from collections import defaultdict


SOURCE = "windows_srum_energy_estimation"
MAX_EXPORT_CHARS = 8 * 1024 * 1024
MAX_ROWS = 100_000
MAX_ENERGY_MJ = 1_000_000_000_000
MAX_APPS = 8


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _app_name(value: str) -> str | None:
    candidate = ntpath.basename(value.strip().replace("/", "\\"))
    if candidate.casefold().endswith(".exe"):
        candidate = candidate[:-4]
    candidate = "".join(
        character for character in candidate
        if character.isprintable() and character not in "<>\"|?*"
    ).strip()
    if not candidate or len(candidate) > 96:
        return None
    return candidate


def _energy_metric(header: str) -> tuple[bool, float]:
    """Return whether this is a unit-bearing energy field and its mJ factor."""
    folded = header.casefold()
    if "energy" not in folded and "consumption" not in folded:
        return False, 0.0
    if re.search(r"(?:\(|\[|\b)\s*m\s*j\s*(?:\)|\]|\b)", folded):
        return True, 1.0
    if re.search(r"(?:\(|\[|\b)\s*m\s*wh\s*(?:\)|\]|\b)", folded):
        return True, 3600.0
    if re.search(r"(?:\(|\[|\b)\s*(?:µ|μ|u)\s*wh\s*(?:\)|\]|\b)", folded):
        return True, 3.6
    if re.search(r"(?:\(|\[|\b)\s*wh\s*(?:\)|\]|\b)", folded):
        return True, 3_600_000.0
    return False, 0.0


def parse_srum_energy_csv(text: str) -> dict:
    """Aggregate app energy estimates without retaining/exporting raw SRUM rows.

    The exporter includes the whole SRUM energy-estimation dataset. This parser
    keeps only a bounded app basename and unit-normalized energy totals. Unknown
    schemas and units fail closed instead of treating CPU time or power as energy.
    """
    unknown = {"state": "unknown", "source": SOURCE, "apps": []}
    if not isinstance(text, str) or not text or len(text) > MAX_EXPORT_CHARS:
        return unknown
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        headers = [header for header in (reader.fieldnames or []) if isinstance(header, str)]
        app_header = next(
            (header for header in headers if _header_key(header) in {
                "appid", "application", "applicationname", "appname", "processname",
            }),
            None,
        )
        metrics = [(header, factor) for header in headers
                   for valid, factor in [_energy_metric(header)] if valid]
        if app_header is None or not metrics:
            return unknown

        # Prefer an explicit total to component columns, avoiding double count.
        totals = [(header, factor) for header, factor in metrics
                  if "total" in header.casefold() or "overall" in header.casefold()]
        if len(totals) > 1:
            return unknown
        selected = totals[:1] if totals else metrics
        energy_by_app: dict[str, float] = defaultdict(float)
        labels: dict[str, str] = {}
        count = 0
        for row in reader:
            count += 1
            if count > MAX_ROWS:
                return unknown
            if not isinstance(row, dict):
                continue
            app_value = row.get(app_header)
            if not isinstance(app_value, str) or len(app_value) > 4096:
                continue
            label = _app_name(app_value)
            if label is None:
                continue
            raw_energy = 0.0
            valid_metric = False
            invalid_metric = False
            for header, factor in selected:
                value = row.get(header)
                if value is None or not value.strip():
                    continue
                try:
                    amount = float(value.strip())
                except (TypeError, ValueError, OverflowError):
                    invalid_metric = True
                    break
                converted = amount * factor
                if (not math.isfinite(amount) or amount < 0 or amount > MAX_ENERGY_MJ
                        or not math.isfinite(converted) or converted > MAX_ENERGY_MJ):
                    invalid_metric = True
                    break
                raw_energy += converted
                valid_metric = True
            if invalid_metric or not valid_metric:
                continue
            if raw_energy > MAX_ENERGY_MJ:
                continue
            key = label.casefold()
            labels.setdefault(key, label)
            energy_by_app[key] += raw_energy

        apps = [
            {"name": labels[key], "energy_mj": int(round(amount)), "quality": "estimated"}
            for key, amount in energy_by_app.items()
            if 0 <= amount <= MAX_ENERGY_MJ
        ]
        apps.sort(key=lambda item: (-item["energy_mj"], item["name"].casefold()))
        apps = apps[:MAX_APPS]
        if not apps:
            return unknown
        return {"state": "supported", "source": SOURCE, "unit": "mJ", "apps": apps}
    except (csv.Error, UnicodeError, TypeError, ValueError, OverflowError):
        return unknown
