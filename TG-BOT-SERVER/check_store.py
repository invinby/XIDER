"""Bounded metadata-only history for explicitly allowlisted read-only checks.

No result bodies or command arguments are accepted by this storage boundary.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading

READ_ONLY_CHECKS = {"cmd:sys_uptime": "sys_uptime", "cmd:autorun_status": "autorun_status",
                    "cmd:smart": "storage_smart"}
OUTCOMES = frozenset({"succeeded", "failed", "unsupported", "needs_permission", "timed_out", "unknown"})
MAX_RECORDS = 256
MAX_BYTES = 131072
FRESH_SECONDS = 150
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DEVICE_ID = re.compile(r"[A-Za-z0-9_-](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9_-])?\Z")
_VERSION = re.compile(r"(?:unknown|v?\d{1,6}\.\d{1,6}(?:\.\d{1,6})?(?:[-+][A-Za-z0-9.-]{1,24})?)\Z")


@dataclass(frozen=True)
class CheckRecord:
    device_id: str
    callback: str
    platform: str
    agent_version: str
    checked_at: str
    latency_ms: int | None
    outcome: str
    correlation_id: str | None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid check timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("check timestamp must be UTC")
        return stamp
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid check timestamp") from exc


def _validate(record):
    if type(record) is not CheckRecord:
        raise TypeError("CheckRecord required; raw payloads are forbidden")
    for field, value in (("device_id", record.device_id), ("correlation_id", record.correlation_id)):
        if value is None and field == "correlation_id":
            continue
        pattern = _DEVICE_ID if field == "device_id" else _IDENTIFIER
        if not isinstance(value, str) or not pattern.fullmatch(value) or ".." in value:
            raise ValueError("invalid check identifier")
    if not isinstance(record.callback, str) or record.callback not in READ_ONLY_CHECKS:
        raise ValueError("callback is not a read-only check")
    if record.platform not in ("windows", "macos", "linux", "unknown"):
        raise ValueError("invalid platform")
    if not isinstance(record.agent_version, str) or not _VERSION.fullmatch(record.agent_version):
        raise ValueError("invalid agent version")
    if not isinstance(record.outcome, str) or record.outcome not in OUTCOMES:
        raise ValueError("invalid check outcome")
    if record.latency_ms is not None and (type(record.latency_ms) is not int or not 0 <= record.latency_ms <= 86400000):
        raise ValueError("invalid check latency")
    _timestamp(record.checked_at)


def is_fresh(record, *, now=None, max_age=FRESH_SECONDS):
    if record is None:
        return False
    _validate(record)
    age = ((now or datetime.now(timezone.utc)) - _timestamp(record.checked_at)).total_seconds()
    return 0 <= age <= max_age


def summary(record, *, now=None):
    if record is None:
        return "Последняя проверка: нет данных."
    freshness = "актуально" if is_fresh(record, now=now) else "устарело — последние известные данные"
    latency = "unknown" if record.latency_ms is None else f"{record.latency_ms} ms"
    return (f"Последняя проверка: {record.outcome} ({freshness})\n"
            f"Время: {record.checked_at} · OS: {record.platform} · агент: {record.agent_version} · задержка: {latency}")


def application_data_path():
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    if not root.is_absolute():
        raise ValueError("application data root must be absolute")
    return root / "XIDER" / "checks.json"


def _safe_path(path):
    # Refuse symlinks/junctions anywhere in the path, including its data directory.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("unsafe check storage path")


class CheckStore:
    def __init__(self, path=None, *, max_records=MAX_RECORDS):
        self.path = Path(path) if path is not None else application_data_path()
        if not self.path.is_absolute() or type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise ValueError("invalid check storage configuration")
        self.max_records = max_records
        self._lock = threading.RLock()
        self._records = {}
        self._trusted = True
        try:
            _safe_path(self.path)
            if self.path.exists():
                if self.path.stat().st_size > MAX_BYTES:
                    raise ValueError("oversized check store")
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if type(value) is not dict or set(value) != {"schema", "records"} or value["schema"] != 1:
                    raise ValueError("invalid check schema")
                rows = value["records"]
                if type(rows) is not list or len(rows) > max_records:
                    raise ValueError("invalid check count")
                for row in rows:
                    record = CheckRecord(**row)
                    _validate(record)
                    key = (record.device_id, record.callback)
                    if key in self._records:
                        raise ValueError("duplicate check key")
                    self._records[key] = record
        except (OSError, ValueError, TypeError):
            self._trusted = False
            self._records = {}

    def get_last_check(self, device_id, callback):
        with self._lock:
            return self._records.get((device_id, callback))

    def record_check(self, record):
        _validate(record)
        with self._lock:
            if not self._trusted:
                raise ValueError("untrusted check store; refusing overwrite")
            _safe_path(self.path)
            key = (record.device_id, record.callback)
            old = self._records.get(key)
            if old and _timestamp(old.checked_at) > _timestamp(record.checked_at):
                return
            proposed = dict(self._records)
            proposed[key] = record
            rows = sorted(proposed.values(), key=lambda item: _timestamp(item.checked_at))[-self.max_records:]
            encoded = json.dumps({"schema": 1, "records": [asdict(row) for row in rows]}, ensure_ascii=True).encode("utf-8")
            if len(encoded) > MAX_BYTES:
                raise ValueError("check store byte limit exceeded")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _safe_path(self.path)
            fd, temporary = tempfile.mkstemp(prefix=".checks-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self._records = {(row.device_id, row.callback): row for row in rows}
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)


_default = None
_default_lock = threading.Lock()


def _store():
    global _default
    with _default_lock:
        if _default is None:
            _default = CheckStore()
        return _default


def record_check(record: CheckRecord) -> None:
    _store().record_check(record)


def get_last_check(device_id: str, callback: str) -> CheckRecord | None:
    return _store().get_last_check(device_id, callback)
