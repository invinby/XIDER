"""Strict, read-only parser and reconciler for owner device-catalog imports."""

import csv
import io
import json
import math


MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ROWS = 500
FIELDS = ("device_id", "name", "os", "version", "online", "last_seen")
TEXT_FIELDS = frozenset({"name", "os", "version"})


def _reject_constant(_value):
    raise ValueError("non-finite numbers are not allowed")


def _normalize_value(field, value):
    if field == "device_id":
        if type(value) is not str or not value or len(value) > 128 or value.strip() != value:
            raise ValueError("invalid device_id")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid device_id")
        return value
    if field in TEXT_FIELDS:
        if value is None:
            return None
        if type(value) is not str or len(value) > 256:
            raise ValueError(f"invalid {field}")
        if any(ord(char) < 32 and char not in "\t\r\n" or ord(char) == 127 for char in value):
            raise ValueError(f"invalid {field}")
        return value
    if field == "online":
        if value is None or type(value) is bool:
            return value
        raise ValueError("invalid online")
    if field == "last_seen":
        if value is None:
            return None
        if type(value) not in (int, float):
            raise ValueError("invalid last_seen")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ValueError("invalid last_seen") from None
        if not math.isfinite(number):
            raise ValueError("invalid last_seen")
        return number
    raise ValueError("unknown catalog field")


def _normalize_records(records):
    if not isinstance(records, list) or len(records) > MAX_IMPORT_ROWS:
        raise ValueError("invalid import rows")
    normalized = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or "device_id" not in record:
            raise ValueError("each row requires device_id")
        if any(type(field) is not str or field not in FIELDS for field in record):
            raise ValueError("unknown catalog field")
        row = {field: _normalize_value(field, value) for field, value in record.items()}
        device_id = row["device_id"]
        if device_id in seen:
            raise ValueError("duplicate device_id")
        seen.add(device_id)
        normalized.append(row)
    return normalized


def parse_device_catalog_import(payload: bytes, filename: str) -> list[dict]:
    """Parse a bounded JSON/CSV export; reject unknown columns and secrets."""
    if type(payload) is not bytes or len(payload) > MAX_IMPORT_BYTES:
        raise ValueError("invalid import size")
    if type(filename) is not str:
        raise ValueError("unsupported import format")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("import must be UTF-8") from None

    suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    if suffix == "json":
        try:
            records = json.loads(text, parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("invalid catalog JSON") from None
        return _normalize_records(records)
    if suffix != "csv":
        raise ValueError("unsupported import format")

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if (not headers or len(headers) != len(set(headers)) or "device_id" not in headers
                or any(header not in FIELDS for header in headers)):
            raise ValueError("invalid catalog CSV header")
        records = []
        for row in reader:
            if len(records) >= MAX_IMPORT_ROWS:
                raise ValueError("invalid import rows")
            if None in row:
                raise ValueError("invalid catalog CSV row")
            for field, value in list(row.items()):
                if field == "online":
                    if value == "":
                        row[field] = None
                    elif value.casefold() in {"true", "false"}:
                        row[field] = value.casefold() == "true"
                    else:
                        raise ValueError("invalid online")
                elif field == "last_seen":
                    if value == "":
                        row[field] = None
                    else:
                        try:
                            row[field] = float(value)
                        except (TypeError, ValueError, OverflowError):
                            raise ValueError("invalid last_seen") from None
            records.append(row)
    except csv.Error:
        raise ValueError("invalid catalog CSV") from None
    return _normalize_records(records)


def compare_device_catalogs(imported: list[dict], current: dict[str, dict]) -> dict:
    """Return a bounded field-level diff without modifying either input."""
    imported = _normalize_records(imported)
    if not isinstance(current, dict) or len(current) > MAX_IMPORT_ROWS:
        raise ValueError("invalid current catalog")
    current_ids = {key for key in current if type(key) is str and key}
    imported_by_id = {row["device_id"]: row for row in imported}
    imported_ids = set(imported_by_id)
    new_ids = sorted(imported_ids - current_ids)
    missing_ids = sorted(current_ids - imported_ids)
    unchanged_ids = []
    conflicts = []
    for device_id in sorted(imported_ids & current_ids):
        existing = current.get(device_id)
        if not isinstance(existing, dict):
            raise ValueError("invalid current catalog row")
        changed = [
            field for field in FIELDS[1:]
            if field in imported_by_id[device_id]
            and imported_by_id[device_id][field] != existing.get(field)
        ]
        if changed:
            conflicts.append({"device_id": device_id, "fields": changed})
        else:
            unchanged_ids.append(device_id)
    return {
        "new_ids": new_ids,
        "missing_ids": missing_ids,
        "unchanged_ids": unchanged_ids,
        "conflicts": conflicts,
    }
