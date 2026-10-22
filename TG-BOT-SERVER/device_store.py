"""Хранилище известных устройств XGENT (JSON-файл рядом с bot.py)."""

import json
import ipaddress
import math
import re
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path


class DeviceStore:
    """Потокобезопасное JSON-хранилище устройств.

    Данные перезаписываются на диск только при фактическом изменении,
    чтобы heartbeat-сообщения (раз в 60 с на устройство) не генерировали
    бессмысленные записи на диск.
    """

    FILE = Path(__file__).resolve().parent / "known_devices.json"
    CATALOG_FILE = Path(__file__).resolve().parent / "device_catalog.json"
    SYSTEM_SUMMARY_TTL_SECONDS = 30 * 24 * 60 * 60
    SYSTEM_SUMMARY_LIMIT = 50
    SYSTEM_SUMMARY_PERIODS = frozenset({3_600, 21_600, 86_400})

    def __init__(self) -> None:
        self._devices: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._catalog_lock = threading.RLock()
        self._catalog: dict = {}
        self._load()
        self._load_catalog()

    def _load(self) -> None:
        try:
            self._devices = json.loads(self.FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self._devices = {}

    def _save(self) -> None:
        tmp = self.FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._devices, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.FILE)

    def _load_catalog(self) -> None:
        try:
            data = json.loads(self.CATALOG_FILE.read_text(encoding="utf-8"))
            self._catalog = data if isinstance(data, dict) and data.get("schema_version") == 1 else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._catalog = {}
        self._catalog.setdefault("schema_version", 1)
        for field in ("snapshots", "device_collections", "saved_device_filters", "device_catalog_sort", "device_catalog_order", "pinned_devices", "field_schemas", "typed_values", "navigation", "feature_search_synonyms", "device_tags", "device_purposes", "inventory_templates", "shell_runbook_templates", "private_device_notes", "device_place_labels", "device_review_dates", "device_maintenance", "archived_devices", "hardware_snapshots", "driver_snapshots", "installed_app_snapshots", "startup_snapshots", "service_snapshots", "smart_trend_snapshots", "wifi_quality_histories", "network_traffic_samples", "network_traffic_alerts", "system_summary_snapshots", "system_summary_schedules", "system_summary_thresholds", "network_check_sets", "external_ip_watches"):
            if not isinstance(self._catalog.get(field), dict):
                self._catalog[field] = {}
        if not isinstance(self._catalog.get("device_relations"), list):
            self._catalog["device_relations"] = []
        if not isinstance(self._catalog.get("inactivity_snapshots"), list):
            self._catalog["inactivity_snapshots"] = []

    @staticmethod
    def _normalize_device_tags(tags: list) -> list[str]:
        if not isinstance(tags, list) or len(tags) > 16:
            raise ValueError("device tags must be a list of at most 16 labels")
        normalized, seen = [], set()
        for tag in tags:
            if not isinstance(tag, str):
                raise ValueError("device tag must be text")
            label = " ".join(tag.split())
            if not label or len(label) > 80 or any(unicodedata.category(ch) == "Cc" for ch in label):
                raise ValueError("invalid device tag")
            key = label.casefold()
            if key not in seen:
                normalized.append(label)
                seen.add(key)
        return normalized

    def set_device_tags(self, actor_id: int, owner_id: int, device_id: str, tags: list) -> bool:
        """Replace owner-managed free-form labels; tags do not change device classification."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        device = str(device_id)
        if not device or len(device) > 128:
            raise ValueError("invalid device id")
        normalized = self._normalize_device_tags(tags)
        with self._catalog_lock:
            rows = self._catalog["device_tags"]
            if normalized:
                rows[device] = normalized
            else:
                rows.pop(device, None)
            self._save_catalog()
        return True

    def get_device_tags(self, actor_id: int, owner_id: int, device_id: str) -> list[str] | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            raw = self._catalog["device_tags"].get(str(device_id), [])
            try:
                return self._normalize_device_tags(raw)
            except ValueError:
                return []

    DEVICE_PURPOSES = frozenset({"personal", "test", "work", "other"})

    def set_device_purpose(self, actor_id: int, owner_id: int, device_id: str, purpose: str | None) -> bool:
        """Set one normalized owner-only device purpose, separate from free-form tags."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        device = str(device_id)
        if not device or len(device) > 128:
            raise ValueError("invalid device id")
        if purpose is not None and (not isinstance(purpose, str) or purpose not in self.DEVICE_PURPOSES):
            raise ValueError("invalid device purpose")
        with self._catalog_lock:
            rows = self._catalog["device_purposes"]
            if purpose is None:
                rows.pop(device, None)
            else:
                rows[device] = purpose
            self._save_catalog()
        return True

    def get_device_purpose(self, actor_id: int, owner_id: int, device_id: str) -> str | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            raw = self._catalog["device_purposes"].get(str(device_id))
            return raw if raw in self.DEVICE_PURPOSES else None

    @staticmethod
    def _normalize_network_check_set_name(name: object) -> str:
        if not isinstance(name, str):
            raise ValueError("network check-set name must be text")
        label = " ".join(name.split())
        if not label or len(label) > 40 or any(unicodedata.category(ch) == "Cc" for ch in label):
            raise ValueError("network check-set name must contain 1-40 printable characters")
        return label

    @staticmethod
    def _normalize_network_check_endpoints(endpoints: object) -> list[dict]:
        if not isinstance(endpoints, list) or not 1 <= len(endpoints) <= 5:
            raise ValueError("network check sets require 1-5 individual endpoints")
        normalized: list[dict] = []
        seen: set[tuple[str, str, int]] = set()
        for endpoint in endpoints:
            if not isinstance(endpoint, dict) or set(endpoint) != {"protocol", "host", "port"}:
                raise ValueError("network endpoint must contain only protocol, host, and port")
            protocol, host, port = endpoint["protocol"], endpoint["host"], endpoint["port"]
            if not isinstance(protocol, str) or protocol not in {"tcp", "tls"} or not isinstance(host, str) or not host or len(host) > 253:
                raise ValueError("invalid network endpoint protocol or host")
            if any(char in host for char in "/\\@?#% ") or any(unicodedata.category(ch) == "Cc" for ch in host):
                raise ValueError("network endpoint host must be a hostname or IP address, not a URL")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if ":" in host:
                    raise ValueError("IPv6 addresses must be stored without a port")
                try:
                    clean_host = host.encode("idna").decode("ascii").lower().rstrip(".")
                except UnicodeError as exc:
                    raise ValueError("invalid network endpoint hostname") from exc
                labels = clean_host.split(".")
                if (len(clean_host) > 253 or not clean_host or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
                )):
                    raise ValueError("invalid network endpoint hostname")
            else:
                clean_host = address.compressed
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("network endpoint port must be an integer from 1 to 65535")
            key = (protocol, clean_host.casefold(), port)
            if key in seen:
                raise ValueError("duplicate network endpoint")
            seen.add(key)
            normalized.append({"protocol": protocol, "host": clean_host, "port": port})
        return normalized

    def save_network_check_set(
        self, actor_id: int, owner_id: int, name: str, endpoints: list[dict], *, now: int | None = None,
    ) -> dict | None:
        """Persist an owner-only, bounded list of individually selected TCP/TLS endpoints."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        label = self._normalize_network_check_set_name(name)
        normalized = self._normalize_network_check_endpoints(endpoints)
        timestamp = int(now if now is not None else time.time())
        if timestamp < 0:
            raise ValueError("invalid network check-set timestamp")
        owner_key = str(int(owner_id))
        with self._catalog_lock:
            owners = self._catalog["network_check_sets"]
            rows = owners.setdefault(owner_key, {})
            if not isinstance(rows, dict):
                rows = owners[owner_key] = {}
            valid_rows = [self.get_network_check_set(actor_id, owner_id, set_id) for set_id in rows]
            valid_rows = [row for row in valid_rows if row is not None]
            if len(valid_rows) >= 20:
                raise ValueError("at most 20 network check sets may be saved")
            if any(str(row.get("name", "")).casefold() == label.casefold() for row in valid_rows):
                raise ValueError("network check-set name already exists")
            record = {
                "set_id": uuid.uuid4().hex, "owner_id": int(owner_id), "name": label,
                "endpoints": normalized, "created_at": timestamp,
            }
            rows[record["set_id"]] = record
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_network_check_sets(self, actor_id: int, owner_id: int) -> list[dict]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        with self._catalog_lock:
            raw = self._catalog["network_check_sets"].get(str(int(owner_id)), {})
            if not isinstance(raw, dict):
                return []
            set_ids = list(raw)
        rows = [self.get_network_check_set(actor_id, owner_id, set_id) for set_id in set_ids]
        valid_rows = [row for row in rows if row is not None]
        valid_rows.sort(key=lambda row: (row["created_at"], row["set_id"]), reverse=True)
        return valid_rows

    def get_network_check_set(self, actor_id: int, owner_id: int, set_id: str) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(set_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return None
        with self._catalog_lock:
            owners = self._catalog["network_check_sets"].get(str(int(owner_id)), {})
            row = owners.get(key) if isinstance(owners, dict) else None
            if not isinstance(row, dict) or row.get("owner_id") != int(owner_id):
                return None
            try:
                created_at = int(row.get("created_at"))
                if created_at < 0:
                    return None
                clean = {
                    "set_id": key, "owner_id": int(owner_id),
                    "name": self._normalize_network_check_set_name(row.get("name")),
                    "endpoints": self._normalize_network_check_endpoints(row.get("endpoints")),
                    "created_at": created_at,
                }
            except (TypeError, ValueError):
                return None
            return clean

    def delete_network_check_set(self, actor_id: int, owner_id: int, set_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(set_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return False
        with self._catalog_lock:
            owners = self._catalog["network_check_sets"]
            rows = owners.get(str(int(owner_id)))
            if not isinstance(rows, dict):
                return False
            record = rows.get(key)
            if not isinstance(record, dict) or record.get("owner_id") != int(owner_id):
                return False
            del rows[key]
            if not rows:
                owners.pop(str(int(owner_id)), None)
            self._save_catalog()
        return True

    EXTERNAL_IP_WATCH_INTERVALS = frozenset({900, 1800, 3600})

    @staticmethod
    def _normalize_external_ip(value: object) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("external IP must be a canonical public IP address")
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("external IP must be a canonical public IP address") from exc
        if not address.is_global or str(address) != value:
            raise ValueError("external IP must be globally routable and canonical")
        return str(address)

    def set_external_ip_watch(
        self, actor_id: int, owner_id: int, device_id: str, interval_seconds: int, *, now: int | None = None,
    ) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(device_id)
        if not key or len(key) > 128:
            raise ValueError("invalid device id")
        if type(interval_seconds) is not int or interval_seconds not in self.EXTERNAL_IP_WATCH_INTERVALS:
            raise ValueError("external IP watch interval is not allowed")
        timestamp = int(now if now is not None else time.time())
        if timestamp < 0:
            raise ValueError("invalid external IP watch timestamp")
        with self._catalog_lock:
            watches = self._catalog["external_ip_watches"]
            previous = watches.get(key)
            if isinstance(previous, dict) and previous.get("owner_id") == int(owner_id):
                record = dict(previous)
                record["interval_seconds"] = interval_seconds
            else:
                owned = sum(isinstance(row, dict) and row.get("owner_id") == int(owner_id)
                            for row in watches.values())
                if owned >= 20:
                    raise ValueError("external IP watch limit reached")
                record = {
                    "device_id": key, "owner_id": int(owner_id), "interval_seconds": interval_seconds,
                    "enabled_at": timestamp, "last_attempt_at": None, "last_checked_at": None, "last_ip": None,
                    "last_observed_at": None, "pending_alerts": [],
                }
            watches[key] = record
            self._save_catalog()
        return json.loads(json.dumps(record))

    def disable_external_ip_watch(self, actor_id: int, owner_id: int, device_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(device_id)
        with self._catalog_lock:
            row = self._catalog["external_ip_watches"].get(key)
            if not isinstance(row, dict) or row.get("owner_id") != int(owner_id):
                return False
            del self._catalog["external_ip_watches"][key]
            self._save_catalog()
        return True

    def list_external_ip_watches(self, actor_id: int, owner_id: int) -> list[dict]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        with self._catalog_lock:
            records = [dict(row) for row in self._catalog["external_ip_watches"].values()
                       if isinstance(row, dict) and row.get("owner_id") == int(owner_id)]
        clean = []
        for row in records:
            try:
                if (not isinstance(row.get("device_id"), str) or not row["device_id"]
                        or type(row.get("interval_seconds")) is not int
                        or row["interval_seconds"] not in self.EXTERNAL_IP_WATCH_INTERVALS):
                    continue
                row["last_attempt_at"] = int(row["last_attempt_at"]) if row.get("last_attempt_at") is not None else None
                row["last_checked_at"] = int(row["last_checked_at"]) if row.get("last_checked_at") is not None else None
                row["enabled_at"] = int(row.get("enabled_at"))
                ip = row.get("last_ip")
                if ip is not None:
                    row["last_ip"] = self._normalize_external_ip(ip)
                alerts = row.get("pending_alerts", [])
                if not isinstance(alerts, list) or len(alerts) > 20:
                    continue
                normalized_alerts = []
                for alert in alerts:
                    if not isinstance(alert, dict) or set(alert) != {"alert_id", "previous_ip", "current_ip", "changed_at"}:
                        continue
                    if not re.fullmatch(r"[0-9a-f]{32}", str(alert["alert_id"])):
                        continue
                    normalized_alerts.append({
                        "alert_id": alert["alert_id"], "previous_ip": self._normalize_external_ip(alert["previous_ip"]),
                        "current_ip": self._normalize_external_ip(alert["current_ip"]), "changed_at": int(alert["changed_at"]),
                    })
                row["pending_alerts"] = normalized_alerts
                clean.append(row)
            except (TypeError, ValueError):
                continue
        return json.loads(json.dumps(clean))

    def record_external_ip_sample(
        self, actor_id: int, owner_id: int, device_id: str, address: str, *, now: int | None = None,
    ) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(device_id)
        ip = self._normalize_external_ip(address)
        timestamp = int(now if now is not None else time.time())
        if timestamp < 0:
            raise ValueError("invalid external IP sample timestamp")
        with self._catalog_lock:
            watch = self._catalog["external_ip_watches"].get(key)
            if not isinstance(watch, dict) or watch.get("owner_id") != int(owner_id):
                return None
            previous = watch.get("last_ip")
            if watch.get("last_observed_at") is not None and timestamp < int(watch["last_observed_at"]):
                raise ValueError("stale external IP sample")
            changed = previous is not None and previous != ip
            alerts = watch.get("pending_alerts")
            if not isinstance(alerts, list):
                alerts = watch["pending_alerts"] = []
            if changed and len(alerts) >= 20:
                raise ValueError("external IP notification queue is full")
            alert = None
            if changed:
                alert = {
                    "alert_id": uuid.uuid4().hex, "previous_ip": previous,
                    "current_ip": ip, "changed_at": timestamp,
                }
                alerts.append(alert)
            watch["last_ip"] = ip
            watch["last_checked_at"] = timestamp
            watch["last_observed_at"] = timestamp
            self._save_catalog()
            return {
                "changed": changed, "previous_ip": previous, "current_ip": ip,
                "changed_at": timestamp if changed else None,
                "alert_id": alert["alert_id"] if alert else None,
            }

    def note_external_ip_attempt(self, actor_id: int, owner_id: int, device_id: str, *, now: int | None = None) -> bool:
        """Persist bounded polling cadence without claiming a successful observation."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(device_id)
        timestamp = int(now if now is not None else time.time())
        if timestamp < 0:
            raise ValueError("invalid external IP attempt timestamp")
        with self._catalog_lock:
            watch = self._catalog["external_ip_watches"].get(key)
            if not isinstance(watch, dict) or watch.get("owner_id") != int(owner_id):
                return False
            previous = watch.get("last_attempt_at")
            if previous is not None and timestamp < int(previous):
                raise ValueError("stale external IP attempt")
            watch["last_attempt_at"] = timestamp
            self._save_catalog()
        return True

    def pending_external_ip_alerts(self, actor_id: int, owner_id: int, device_id: str) -> list[dict] | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(device_id)
        with self._catalog_lock:
            row = self._catalog["external_ip_watches"].get(key)
            if not isinstance(row, dict) or row.get("owner_id") != int(owner_id):
                return None
            alerts = row.get("pending_alerts", [])
            if not isinstance(alerts, list) or len(alerts) > 20:
                return []
            try:
                return json.loads(json.dumps([
                    {
                        "alert_id": str(alert["alert_id"]),
                        "previous_ip": self._normalize_external_ip(alert["previous_ip"]),
                        "current_ip": self._normalize_external_ip(alert["current_ip"]),
                        "changed_at": int(alert["changed_at"]),
                    }
                    for alert in alerts if isinstance(alert, dict)
                    and set(alert) == {"alert_id", "previous_ip", "current_ip", "changed_at"}
                    and re.fullmatch(r"[0-9a-f]{32}", str(alert.get("alert_id", "")))
                ]))
            except (TypeError, ValueError):
                return []

    def mark_external_ip_alert_delivered(
        self, actor_id: int, owner_id: int, device_id: str, alert_id: str,
    ) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key, token = str(device_id), str(alert_id)
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            return False
        with self._catalog_lock:
            row = self._catalog["external_ip_watches"].get(key)
            if not isinstance(row, dict) or row.get("owner_id") != int(owner_id):
                return False
            alerts = row.get("pending_alerts")
            if not isinstance(alerts, list):
                return False
            remaining = [item for item in alerts if not (isinstance(item, dict) and item.get("alert_id") == token)]
            if len(remaining) == len(alerts):
                return False
            row["pending_alerts"] = remaining
            self._save_catalog()
        return True

    @staticmethod
    def _normalize_relation_id(device_id: str) -> str:
        if (type(device_id) is not str or not device_id or len(device_id) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in device_id)):
            raise ValueError("invalid device relation ID")
        return device_id

    @classmethod
    def _normalize_device_relations(cls, raw) -> set[tuple[str, str]]:
        if not isinstance(raw, list):
            return set()
        pairs = set()
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                left, right = (cls._normalize_relation_id(value) for value in item)
            except ValueError:
                continue
            if left == right:
                continue
            pairs.add(tuple(sorted((left, right), key=lambda value: (value.casefold(), value))))
        return pairs

    def set_device_relation(
        self, actor_id: int, owner_id: int, device_a: str, device_b: str, linked: bool,
    ) -> bool:
        """Set an owner-authored, symmetric catalog link; it never changes device state."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        left = self._normalize_relation_id(device_a)
        right = self._normalize_relation_id(device_b)
        if left == right or type(linked) is not bool:
            raise ValueError("invalid device relation")
        pair = tuple(sorted((left, right), key=lambda value: (value.casefold(), value)))
        with self._lock:
            if left not in self._devices or right not in self._devices:
                raise ValueError("device relation requires two existing devices")
            with self._catalog_lock:
                pairs = self._normalize_device_relations(self._catalog.get("device_relations", []))
                if linked:
                    if pair in pairs:
                        return True
                    if any(left in existing for existing in pairs) and sum(left in existing for existing in pairs) >= 16:
                        raise ValueError("device relation limit reached")
                    if sum(right in existing for existing in pairs) >= 16:
                        raise ValueError("device relation limit reached")
                    pairs.add(pair)
                else:
                    pairs.discard(pair)
                self._catalog["device_relations"] = [list(value) for value in sorted(pairs)]
                self._save_catalog()
        return True

    def get_device_relations(self, actor_id: int, owner_id: int, device_id: str) -> list[str] | None:
        """Return live linked IDs for the owner; stale IDs never become visible."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        device = self._normalize_relation_id(device_id)
        with self._catalog_lock:
            pairs = self._normalize_device_relations(self._catalog.get("device_relations", []))
        with self._lock:
            live_ids = set(self._devices)
        if device not in live_ids:
            return []
        peers = {
            right if left == device else left
            for left, right in pairs if device in (left, right)
        }
        return sorted(peers & live_ids, key=lambda value: (value.casefold(), value))

    @staticmethod
    def _normalize_inventory_template_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValueError("inventory template name must be text")
        normalized = " ".join(name.split())
        if (not normalized or len(normalized) > 80
                or any(unicodedata.category(char) == "Cc" for char in normalized)):
            raise ValueError("inventory template name must contain 1 to 80 printable characters")
        return normalized

    @staticmethod
    def _typed_value_matches_schema(schema: dict, value) -> bool:
        if not isinstance(schema, dict):
            return False
        kind = schema.get("type")
        if kind == "enum":
            return isinstance(value, str) and value in schema.get("choices", [])
        if kind == "date":
            if not isinstance(value, str):
                return False
            try:
                return date.fromisoformat(value).isoformat() == value
            except ValueError:
                return False
        if kind == "boolean":
            return type(value) is bool
        return False

    @staticmethod
    def _valid_inventory_template(template_id: str, row, owner_id: int) -> bool:
        if (not isinstance(row, dict) or row.get("template_id") != template_id
                or row.get("owner_id") != int(owner_id)
                or type(row.get("created_at")) is not int or row["created_at"] < 0
                or not re.fullmatch(r"[0-9a-f]{32}", template_id)):
            return False
        try:
            DeviceStore._normalize_inventory_template_name(row.get("name"))
            DeviceStore._normalize_device_tags(row.get("tags"))
        except (TypeError, ValueError):
            return False
        purpose = row.get("purpose")
        typed_values = row.get("typed_values")
        return (
            (purpose is None or (isinstance(purpose, str) and purpose in DeviceStore.DEVICE_PURPOSES))
            and isinstance(typed_values, dict)
            and len(typed_values) <= 64
        )

    @staticmethod
    def _normalize_shell_runbook_commands(commands) -> list[str]:
        if not isinstance(commands, (list, tuple)) or not 2 <= len(commands) <= 8:
            raise ValueError("runbook template must contain 2-8 commands")
        normalized = []
        total_bytes = len(commands) - 1
        for command in commands:
            if (not isinstance(command, str) or not command.strip() or "\x00" in command
                    or "\n" in command or "\r" in command):
                raise ValueError("runbook template commands must be non-empty single lines")
            try:
                command_bytes = len(command.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("runbook template commands must be valid UTF-8") from exc
            total_bytes += command_bytes
            if total_bytes > 3000:
                raise ValueError("runbook template exceeds the preview byte limit")
            normalized.append(command)
        return normalized

    @staticmethod
    def _valid_shell_runbook_template(template_id: str, row, owner_id: int) -> bool:
        if (not isinstance(row, dict) or row.get("template_id") != template_id
                or row.get("owner_id") != int(owner_id)
                or not re.fullmatch(r"[0-9a-f]{32}", template_id)):
            return False
        try:
            name = DeviceStore._normalize_inventory_template_name(row.get("name"))
            commands = DeviceStore._normalize_shell_runbook_commands(row.get("commands"))
        except (TypeError, ValueError):
            return False
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")
        return (
            name == row.get("name") and commands == row.get("commands")
            and type(created_at) is int and created_at >= 0
            and type(updated_at) is int and updated_at >= created_at
        )

    def create_shell_runbook_template(
        self, actor_id: int, owner_id: int, name: str, commands, *, now: int | None = None,
    ) -> dict | None:
        """Persist owner-authored runbook text only; this never creates or approves an execution plan."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        normalized_name = self._normalize_inventory_template_name(name)
        normalized_commands = self._normalize_shell_runbook_commands(commands)
        if now is not None and type(now) is not int:
            raise ValueError("runbook template timestamp must be an integer")
        created_at = int(time.time()) if now is None else now
        if created_at < 0:
            raise ValueError("runbook template timestamp must be non-negative")
        record = {
            "template_id": uuid.uuid4().hex,
            "owner_id": int(owner_id),
            "name": normalized_name,
            "commands": normalized_commands,
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self._catalog_lock:
            rows = self._catalog["shell_runbook_templates"]
            valid_rows = [
                row for key, row in rows.items()
                if self._valid_shell_runbook_template(key, row, owner_id)
            ]
            if len(valid_rows) >= 50:
                raise ValueError("runbook template limit reached")
            if any(row["name"].casefold() == normalized_name.casefold() for row in valid_rows):
                raise ValueError("runbook template name already exists")
            rows[record["template_id"]] = record
            self._save_catalog()
        return json.loads(json.dumps(record, ensure_ascii=False))

    def list_shell_runbook_templates(self, actor_id: int, owner_id: int) -> list[dict] | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            rows = [
                json.loads(json.dumps(row, ensure_ascii=False))
                for key, row in self._catalog["shell_runbook_templates"].items()
                if self._valid_shell_runbook_template(key, row, owner_id)
            ]
        return sorted(rows, key=lambda row: (-row["updated_at"], row["name"].casefold(), row["template_id"]))

    def get_shell_runbook_template(self, actor_id: int, owner_id: int, template_id: str) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            row = self._catalog["shell_runbook_templates"].get(str(template_id))
            if not self._valid_shell_runbook_template(str(template_id), row, owner_id):
                return None
            return json.loads(json.dumps(row, ensure_ascii=False))

    def rename_shell_runbook_template(
        self, actor_id: int, owner_id: int, template_id: str, name: str, *, now: int | None = None,
    ) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        normalized_name = self._normalize_inventory_template_name(name)
        updated_at = int(time.time()) if now is None else now
        if type(updated_at) is not int or updated_at < 0:
            raise ValueError("runbook template timestamp must be non-negative")
        key = str(template_id)
        with self._catalog_lock:
            rows = self._catalog["shell_runbook_templates"]
            current = rows.get(key)
            if not self._valid_shell_runbook_template(key, current, owner_id):
                return None
            if updated_at < current["updated_at"]:
                raise ValueError("runbook template timestamp cannot go backwards")
            if any(
                other_id != key and self._valid_shell_runbook_template(other_id, row, owner_id)
                and row["name"].casefold() == normalized_name.casefold()
                for other_id, row in rows.items()
            ):
                raise ValueError("runbook template name already exists")
            renamed = {**current, "name": normalized_name, "updated_at": updated_at}
            rows[key] = renamed
            self._save_catalog()
        return json.loads(json.dumps(renamed, ensure_ascii=False))

    def delete_shell_runbook_template(self, actor_id: int, owner_id: int, template_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(template_id)
        with self._catalog_lock:
            rows = self._catalog["shell_runbook_templates"]
            if not self._valid_shell_runbook_template(key, rows.get(key), owner_id):
                return False
            del rows[key]
            self._save_catalog()
        return True

    def create_device_inventory_template(
        self, actor_id: int, owner_id: int, name: str, source_device_id: str, *, now: int | None = None,
    ) -> dict | None:
        """Capture only owner-managed tags, purpose and validated typed values, never agent reports."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        source = str(source_device_id)
        if not source or self.get(source) is None:
            return None
        normalized_name = self._normalize_inventory_template_name(name)
        tags = self.get_device_tags(actor_id, owner_id, source) or []
        purpose = self.get_device_purpose(actor_id, owner_id, source)
        schemas = self.typed_field_schemas(actor_id, owner_id) or {}
        values = self.typed_fields(actor_id, owner_id, source) or {}
        safe_values = {
            key: value for key, value in values.items()
            if key in schemas and self._typed_value_matches_schema(schemas[key], value)
        }
        if now is not None and type(now) is not int:
            raise ValueError("inventory template timestamp must be an integer")
        created_at = int(time.time()) if now is None else now
        if created_at < 0:
            raise ValueError("inventory template timestamp must be non-negative")
        template_id = uuid.uuid4().hex
        record = {
            "template_id": template_id,
            "owner_id": int(owner_id),
            "name": normalized_name,
            "tags": tags,
            "purpose": purpose,
            "typed_values": safe_values,
            "created_at": created_at,
        }
        with self._catalog_lock:
            templates = self._catalog["inventory_templates"]
            if len(templates) >= 50:
                raise ValueError("inventory template limit reached")
            if any(
                self._valid_inventory_template(existing_id, row, owner_id)
                and row["name"].casefold() == normalized_name.casefold()
                for existing_id, row in templates.items()
            ):
                raise ValueError("inventory template name already exists")
            templates[template_id] = record
            self._save_catalog()
        return json.loads(json.dumps(record, ensure_ascii=False))

    def list_device_inventory_templates(self, actor_id: int, owner_id: int) -> list[dict] | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            rows = [
                json.loads(json.dumps(row, ensure_ascii=False))
                for template_id, row in self._catalog["inventory_templates"].items()
                if self._valid_inventory_template(template_id, row, owner_id)
            ]
        return sorted(rows, key=lambda row: (-row["created_at"], row["name"].casefold(), row["template_id"]))

    def apply_device_inventory_template(
        self, actor_id: int, owner_id: int, template_id: str, destination_device_id: str,
    ) -> bool:
        """Apply an owner template only to a known device with empty owner-managed inventory metadata."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        destination = str(destination_device_id)
        if not destination or self.get(destination) is None:
            return False
        with self._catalog_lock:
            row = self._catalog["inventory_templates"].get(str(template_id))
            if not self._valid_inventory_template(str(template_id), row, owner_id):
                return False
            template = json.loads(json.dumps(row, ensure_ascii=False))
            schemas = self.typed_field_schemas(actor_id, owner_id) or {}
            safe_values = {
                key: value for key, value in template["typed_values"].items()
                if key in schemas and self._typed_value_matches_schema(schemas[key], value)
            }
            if len(safe_values) != len(template["typed_values"]):
                return False
            if template["purpose"] is not None and template["purpose"] not in self.DEVICE_PURPOSES:
                return False
            normalized_tags = self._normalize_device_tags(template["tags"])
            rows = self._catalog
            existing_values = rows["typed_values"].get(destination, {})
            if (rows["device_tags"].get(destination) or rows["device_purposes"].get(destination)
                    or (isinstance(existing_values, dict) and existing_values)):
                raise ValueError("destination device must have empty inventory metadata")
            if not isinstance(existing_values, dict):
                raise ValueError("destination device metadata is malformed")
            if normalized_tags:
                rows["device_tags"][destination] = normalized_tags
            if template["purpose"] is not None:
                rows["device_purposes"][destination] = template["purpose"]
            if safe_values:
                rows["typed_values"][destination] = safe_values
            self._save_catalog()
        return True

    @staticmethod
    def _normalize_private_device_note(note: str) -> str:
        if not isinstance(note, str):
            raise ValueError("private device note must be text")
        if len(note) > 2000:
            raise ValueError("private device note must be at most 2000 characters")
        lines = []
        for line in note.splitlines():
            normalized = " ".join(line.split())
            if any(unicodedata.category(char) == "Cc" for char in normalized):
                raise ValueError("private device note contains control characters")
            lines.append(normalized)
        return "\n".join(lines).strip()

    def set_private_device_note(self, actor_id: int, owner_id: int, device_id: str, note: str) -> bool:
        """Store an owner-only note locally; never add it to device reports or audit payloads."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        device = str(device_id)
        if not device or len(device) > 128:
            raise ValueError("invalid device id")
        normalized = self._normalize_private_device_note(note)
        with self._catalog_lock:
            rows = self._catalog["private_device_notes"]
            if normalized:
                rows[device] = normalized
            else:
                rows.pop(device, None)
            self._save_catalog()
        return True

    def get_private_device_note(self, actor_id: int, owner_id: int, device_id: str) -> str | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            raw = self._catalog["private_device_notes"].get(str(device_id), "")
            try:
                return self._normalize_private_device_note(raw)
            except ValueError:
                return ""

    @staticmethod
    def _normalize_device_place_label(label: str) -> str:
        if not isinstance(label, str):
            raise ValueError("device place must be a text label")
        normalized = " ".join(label.split())
        if len(normalized) > 100 or any(unicodedata.category(char) == "Cc" for char in normalized):
            raise ValueError("device place must be a text label of at most 100 characters")
        if re.fullmatch(r"-?\d{1,2}(?:\.\d+)?,\s*-?\d{1,3}(?:\.\d+)?", normalized):
            raise ValueError("device place must be a text label, not coordinates")
        return normalized

    def set_device_place_label(self, actor_id: int, owner_id: int, device_id: str, label: str) -> bool:
        """Set an owner-authored place name; this field is never a location fix."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        device = str(device_id)
        if not device or len(device) > 128:
            raise ValueError("invalid device id")
        normalized = self._normalize_device_place_label(label)
        with self._catalog_lock:
            rows = self._catalog["device_place_labels"]
            if normalized:
                rows[device] = normalized
            else:
                rows.pop(device, None)
            self._save_catalog()
        return True

    def get_device_place_label(self, actor_id: int, owner_id: int, device_id: str) -> str | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            raw = self._catalog["device_place_labels"].get(str(device_id), "")
            try:
                return self._normalize_device_place_label(raw)
            except ValueError:
                return ""

    @staticmethod
    def _normalize_device_review_date(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("review date must be YYYY-MM-DD text")
        normalized = value.strip()
        if not normalized:
            return ""
        try:
            parsed = date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("review date must be a valid YYYY-MM-DD date") from exc
        if parsed.isoformat() != normalized:
            raise ValueError("review date must use YYYY-MM-DD format")
        return normalized

    def set_device_review_date(self, actor_id: int, owner_id: int, device_id: str, review_date: str) -> bool:
        """Set or clear an owner-authored reminder date; no agent action is sent."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        device = str(device_id)
        if not device or len(device) > 128:
            raise ValueError("invalid device id")
        normalized = self._normalize_device_review_date(review_date)
        with self._catalog_lock:
            rows = self._catalog["device_review_dates"]
            if normalized:
                rows[device] = normalized
            else:
                rows.pop(device, None)
            self._save_catalog()
        return True

    def get_device_review_date(self, actor_id: int, owner_id: int, device_id: str) -> str | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            raw = self._catalog["device_review_dates"].get(str(device_id), "")
            try:
                normalized = self._normalize_device_review_date(raw)
            except ValueError:
                return ""
        return normalized or None

    def set_device_maintenance(
        self, actor_id: int, owner_id: int, device_id: str, until_date: str,
        *, exclude_inactivity: bool = True,
    ) -> bool:
        """Set a temporary owner-only catalog flag; it never controls the agent."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        device = str(device_id)
        if not device or len(device) > 128:
            raise ValueError("invalid device id")
        if type(exclude_inactivity) is not bool:
            raise ValueError("exclude_inactivity must be boolean")
        normalized = self._normalize_device_review_date(until_date)
        with self._catalog_lock:
            rows = self._catalog["device_maintenance"]
            if normalized:
                rows[device] = {"until_date": normalized, "exclude_inactivity": exclude_inactivity}
            else:
                rows.pop(device, None)
            self._save_catalog()
        return True

    def get_device_maintenance(self, actor_id: int, owner_id: int, device_id: str, *, now: int | None = None) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:
            raise ValueError("maintenance check time must be a non-negative integer")
        with self._catalog_lock:
            raw = self._catalog["device_maintenance"].get(str(device_id))
            if not isinstance(raw, dict) or type(raw.get("exclude_inactivity")) is not bool:
                return None
            try:
                until_date = self._normalize_device_review_date(raw.get("until_date"))
            except ValueError:
                return None
            if not until_date:
                return None
        today = datetime.fromtimestamp(current, tz=timezone.utc).date().isoformat()
        return {
            "until_date": until_date,
            "exclude_inactivity": raw["exclude_inactivity"],
            "active": until_date >= today,
        }

    @staticmethod
    def _normalize_collection_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValueError("collection name must be text")
        normalized = " ".join(name.split())
        if (not normalized or len(normalized) > 80
                or any(unicodedata.category(char) == "Cc" for char in normalized)):
            raise ValueError("collection name must contain 1 to 80 printable characters")
        return normalized

    def _normalize_collection_members(self, device_ids: list[str]) -> list[str]:
        if not isinstance(device_ids, list) or not 1 <= len(device_ids) <= 500:
            raise ValueError("collection must contain between 1 and 500 devices")
        members, seen = [], set()
        for item in device_ids:
            if not isinstance(item, str) or not item or len(item) > 128 or any(ord(char) < 32 for char in item):
                raise ValueError("invalid device ID in collection")
            if item not in seen:
                members.append(item)
                seen.add(item)
        with self._lock:
            missing = [item for item in members if item not in self._devices]
        if missing:
            raise ValueError("collection contains unknown device IDs")
        return members

    @staticmethod
    def _valid_device_collection(record, *, collection_id: str, owner_id: int) -> bool:
        return (
            isinstance(record, dict)
            and record.get("collection_id") == collection_id
            and record.get("owner_id") == int(owner_id)
            and isinstance(record.get("name"), str)
            and isinstance(record.get("device_ids"), list)
            and 1 <= len(record["device_ids"]) <= 500
            and all(isinstance(item, str) and item and len(item) <= 128 for item in record["device_ids"])
            and type(record.get("created_at")) is int and record["created_at"] >= 0
            and type(record.get("updated_at")) is int and record["updated_at"] >= record["created_at"]
        )

    def create_device_collection(
        self, actor_id: int, owner_id: int, name: str, device_ids: list[str], *,
        now: int | None = None,
    ) -> dict | None:
        """Create a durable owner-managed group, distinct from expiring device snapshots."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        label = self._normalize_collection_name(name)
        members = self._normalize_collection_members(device_ids)
        created = int(now if now is not None else time.time())
        if created < 0:
            raise ValueError("invalid collection timestamp")
        with self._catalog_lock:
            collections = self._catalog["device_collections"]
            owner_rows = [row for row in collections.values()
                          if isinstance(row, dict) and row.get("owner_id") == int(owner_id)]
            if len(owner_rows) >= 100:
                raise ValueError("owner collection limit reached")
            if any(str(row.get("name", "")).casefold() == label.casefold() for row in owner_rows):
                raise ValueError("collection name already exists")
            record = {
                "collection_id": uuid.uuid4().hex,
                "owner_id": int(owner_id),
                "name": label,
                "device_ids": members,
                "created_at": created,
                "updated_at": created,
            }
            collections[record["collection_id"]] = record
            self._save_catalog()
        return self.get_device_collection(actor_id, owner_id, record["collection_id"])

    def get_device_collection(
        self, actor_id: int, owner_id: int, collection_id: str,
    ) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(collection_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return None
        with self._catalog_lock:
            record = self._catalog["device_collections"].get(key)
            if not self._valid_device_collection(record, collection_id=key, owner_id=owner_id):
                return None
            saved_ids = list(dict.fromkeys(record["device_ids"]))
            result = dict(record)
        with self._lock:
            live = set(self._devices)
        result["device_ids"] = [item for item in saved_ids if item in live]
        result["stale_device_ids"] = [item for item in saved_ids if item not in live]
        return result

    def list_device_collections(
        self, *, actor_id: int, owner_id: int, limit: int = 100,
    ) -> list[dict]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("collection limit must be between 1 and 100")
        with self._catalog_lock:
            ids = [str(key) for key, record in self._catalog["device_collections"].items()
                   if self._valid_device_collection(record, collection_id=str(key), owner_id=owner_id)]
        rows = [self.get_device_collection(actor_id, owner_id, key) for key in ids]
        rows = [row for row in rows if row is not None]
        rows.sort(key=lambda row: (row["updated_at"], row["collection_id"]), reverse=True)
        return rows[:limit]

    def update_device_collection(
        self, actor_id: int, owner_id: int, collection_id: str, *,
        name: str | None = None, device_ids: list[str] | None = None,
        now: int | None = None,
    ) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(collection_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return None
        normalized_name = self._normalize_collection_name(name) if name is not None else None
        normalized_members = self._normalize_collection_members(device_ids) if device_ids is not None else None
        updated = int(now if now is not None else time.time())
        if updated < 0:
            raise ValueError("invalid collection timestamp")
        with self._catalog_lock:
            collections = self._catalog["device_collections"]
            current = collections.get(key)
            if not self._valid_device_collection(current, collection_id=key, owner_id=owner_id):
                return None
            current_name = normalized_name if normalized_name is not None else current["name"]
            if any(
                other_id != key and isinstance(other, dict)
                and other.get("owner_id") == int(owner_id)
                and str(other.get("name", "")).casefold() == current_name.casefold()
                for other_id, other in collections.items()
            ):
                raise ValueError("collection name already exists")
            current["name"] = current_name
            if normalized_members is not None:
                current["device_ids"] = normalized_members
            current["updated_at"] = max(updated, current["created_at"])
            self._save_catalog()
        return self.get_device_collection(actor_id, owner_id, key)

    def delete_device_collection(self, actor_id: int, owner_id: int, collection_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(collection_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return False
        with self._catalog_lock:
            record = self._catalog["device_collections"].get(key)
            if not self._valid_device_collection(record, collection_id=key, owner_id=owner_id):
                return False
            del self._catalog["device_collections"][key]
            self._save_catalog()
        return True

    @staticmethod
    def _normalize_saved_filter_query(query: str) -> str:
        if not isinstance(query, str):
            raise ValueError("saved filter query must be text")
        normalized = " ".join(query.split())
        if not normalized or len(normalized) > 120 or any(unicodedata.category(ch) == "Cc" for ch in normalized):
            raise ValueError("saved filter query must contain 1 to 120 printable characters")
        return normalized

    @staticmethod
    def _valid_saved_device_filter(record, *, filter_id: str, owner_id: int) -> bool:
        return (
            isinstance(record, dict)
            and record.get("filter_id") == filter_id
            and record.get("owner_id") == int(owner_id)
            and record.get("filter_type") in {"os", "heartbeat", "version", "capability", "inventory_age"}
            and isinstance(record.get("name"), str) and 1 <= len(record["name"]) <= 80
            and isinstance(record.get("query"), str) and 1 <= len(record["query"]) <= 120
            and type(record.get("created_at")) is int and record["created_at"] >= 0
            and type(record.get("updated_at")) is int and record["updated_at"] >= record["created_at"]
        )

    def create_saved_device_filter(
        self, actor_id: int, owner_id: int, name: str, filter_type: str,
        query: str, *, now: int | None = None,
    ) -> dict | None:
        """Persist an owner-named filter rule; it never stores a device result snapshot."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        label = self._normalize_collection_name(name)
        if filter_type not in {"os", "heartbeat", "version", "capability", "inventory_age"}:
            raise ValueError("unknown saved filter type")
        normalized_query = self._normalize_saved_filter_query(query)
        created = int(now if now is not None else time.time())
        if created < 0:
            raise ValueError("invalid saved filter timestamp")
        with self._catalog_lock:
            saved_filters = self._catalog["saved_device_filters"]
            owner_rows = [row for row in saved_filters.values()
                          if isinstance(row, dict) and row.get("owner_id") == int(owner_id)]
            if len(owner_rows) >= 100:
                raise ValueError("saved filter limit reached")
            if any(str(row.get("name", "")).casefold() == label.casefold() for row in owner_rows):
                raise ValueError("saved filter name already exists")
            record = {
                "filter_id": uuid.uuid4().hex, "owner_id": int(owner_id),
                "name": label, "filter_type": filter_type, "query": normalized_query,
                "created_at": created, "updated_at": created,
            }
            saved_filters[record["filter_id"]] = record
            self._save_catalog()
        return dict(record)

    def get_saved_device_filter(self, actor_id: int, owner_id: int, filter_id: str) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        key = str(filter_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return None
        with self._catalog_lock:
            record = self._catalog["saved_device_filters"].get(key)
            if not self._valid_saved_device_filter(record, filter_id=key, owner_id=owner_id):
                return None
            return dict(record)

    def list_saved_device_filters(
        self, *, actor_id: int, owner_id: int, limit: int = 100,
    ) -> list[dict]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("saved filter limit must be between 1 and 100")
        with self._catalog_lock:
            rows = [dict(record) for key, record in self._catalog["saved_device_filters"].items()
                       if self._valid_saved_device_filter(record, filter_id=str(key), owner_id=owner_id)]
        rows.sort(key=lambda row: (row["updated_at"], row["filter_id"]), reverse=True)
        return rows[:limit]

    def delete_saved_device_filter(self, actor_id: int, owner_id: int, filter_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(filter_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return False
        with self._catalog_lock:
            record = self._catalog["saved_device_filters"].get(key)
            if not self._valid_saved_device_filter(record, filter_id=key, owner_id=owner_id):
                return False
            del self._catalog["saved_device_filters"][key]
            self._save_catalog()
        return True

    DEVICE_CATALOG_SORTS = frozenset({"name", "platform", "version", "last_seen", "custom"})

    def set_device_catalog_sort(self, actor_id: int, owner_id: int, sort_by: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        if sort_by not in self.DEVICE_CATALOG_SORTS:
            raise ValueError("unknown device catalog sort")
        with self._catalog_lock:
            self._catalog["device_catalog_sort"][str(int(owner_id))] = sort_by
            self._save_catalog()
        return True

    def get_device_catalog_sort(self, actor_id: int, owner_id: int) -> str | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            value = self._catalog["device_catalog_sort"].get(str(int(owner_id)), "name")
        return value if value in self.DEVICE_CATALOG_SORTS else "name"

    def set_device_catalog_order(self, actor_id: int, owner_id: int, device_ids: list[str]) -> bool:
        """Persist an exact owner-managed order for currently registered devices."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        if not isinstance(device_ids, list) or len(device_ids) > 500:
            raise ValueError("device catalog order must contain at most 500 IDs")
        if any(not isinstance(item, str) or not item or len(item) > 128 or any(ord(ch) < 32 for ch in item)
               for item in device_ids):
            raise ValueError("invalid device ID in catalog order")
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("duplicate device ID in catalog order")
        with self._lock:
            if any(item not in self._devices for item in device_ids):
                raise ValueError("catalog order contains an unknown device")
        with self._catalog_lock:
            self._catalog["device_catalog_order"][str(int(owner_id))] = list(device_ids)
            self._save_catalog()
        return True

    def get_device_catalog_order(
        self, actor_id: int, owner_id: int, available_device_ids: list[str],
    ) -> list[str] | None:
        """Return saved live IDs followed by newly available IDs, pruning stale entries."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        if not isinstance(available_device_ids, list) or len(available_device_ids) > 500:
            raise ValueError("available device list must contain at most 500 IDs")
        available, seen = [], set()
        for item in available_device_ids:
            if not isinstance(item, str) or not item or len(item) > 128 or any(ord(ch) < 32 for ch in item):
                raise ValueError("invalid available device ID")
            if item not in seen:
                available.append(item)
                seen.add(item)
        owner_key = str(int(owner_id))
        with self._catalog_lock:
            raw = self._catalog["device_catalog_order"].get(owner_key, [])
            if not isinstance(raw, list):
                raw = []
            saved = list(dict.fromkeys(item for item in raw if isinstance(item, str) and item in seen))
            result = saved + [item for item in available if item not in set(saved)]
            if raw != result:
                self._catalog["device_catalog_order"][owner_key] = result
                self._save_catalog()
            return result

    PINNED_DEVICE_LIMIT = 20
    # Telegram allows at most 64 bytes per callback_data; the toggle callback
    # prefix ``catalog:pinned:toggle:`` consumes 22 bytes.
    PINNED_DEVICE_ID_MAX_BYTES = 42

    @classmethod
    def _valid_pinned_device_id(cls, value: object) -> bool:
        if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
            return False
        try:
            return len(value.encode("utf-8")) <= cls.PINNED_DEVICE_ID_MAX_BYTES
        except UnicodeEncodeError:
            return False

    def pin_device(self, actor_id: int, owner_id: int, device_id: str) -> bool:
        """Pin one known device for the owner, separately from action favorites."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(device_id)
        if not self._valid_pinned_device_id(key):
            return False
        with self._lock:
            if key not in self._devices:
                return False
            live_ids = set(self._devices)
        with self._catalog_lock:
            raw = self._catalog["pinned_devices"].get(str(int(owner_id)), [])
            # Prune removed/invalid IDs while touching the list so stale pins
            # cannot consume the owner's bounded capacity permanently.
            rows = []
            seen = set()
            if isinstance(raw, list):
                for item in raw:
                    if not self._valid_pinned_device_id(item) or item not in live_ids or item in seen:
                        continue
                    rows.append(item)
                    seen.add(item)
            if key not in rows:
                if len(rows) >= self.PINNED_DEVICE_LIMIT:
                    return False
                rows.append(key)
            self._catalog["pinned_devices"][str(int(owner_id))] = rows
            self._save_catalog()
        return True

    def unpin_device(self, actor_id: int, owner_id: int, device_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        owner_key, key = str(int(owner_id)), str(device_id)
        with self._catalog_lock:
            rows = self._catalog["pinned_devices"].get(owner_key, [])
            if not isinstance(rows, list) or key not in rows:
                return False
            remaining = [item for item in rows if item != key]
            if remaining:
                self._catalog["pinned_devices"][owner_key] = remaining
            else:
                self._catalog["pinned_devices"].pop(owner_key, None)
            self._save_catalog()
        return True

    def list_pinned_devices(self, *, actor_id: int, owner_id: int) -> list[str]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        with self._catalog_lock:
            raw = self._catalog["pinned_devices"].get(str(int(owner_id)), [])
            saved = []
            seen = set()
            if isinstance(raw, list):
                for item in raw:
                    if not self._valid_pinned_device_id(item) or item in seen:
                        continue
                    saved.append(item)
                    seen.add(item)
        with self._lock:
            live = set(self._devices)
        return [item for item in saved if item in live]

    def _save_catalog(self) -> None:
        self.CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.CATALOG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.CATALOG_FILE)

    def upsert(
        self, device_id: str, info: dict, *, registration_event: str | None = None,
        now: int | None = None,
    ) -> None:
        if registration_event is not None and registration_event not in {
            "heartbeat", "manual_add", "re_registration",
        }:
            raise ValueError("invalid registration event")
        with self._lock:
            old = self._devices.get(device_id)
            merged = {**(old or {}), **info}
            if registration_event is not None:
                timestamp = int(now if now is not None else time.time())
                if timestamp < 0:
                    raise ValueError("invalid registration timestamp")
                if old is None:
                    merged.update({
                        "registration_origin": registration_event,
                        "registration_count": 1,
                        "registered_at": timestamp,
                        "last_registered_at": timestamp,
                        "last_registration_event": registration_event,
                    })
                else:
                    merged.setdefault("registration_origin", "unknown")
                    merged.setdefault("registration_count", 0)
                    merged.setdefault("registered_at", 0)
                    merged.setdefault("last_registered_at", 0)
                    if registration_event == "re_registration":
                        count = merged.get("registration_count")
                        merged["registration_count"] = count + 1 if type(count) is int and count >= 0 else 1
                        merged["last_registered_at"] = timestamp
                        merged["last_registration_event"] = registration_event
            if old == merged:
                return
            self._devices[device_id] = merged
            self._save()

    @staticmethod
    def _duplicate_platform(os_name) -> str | None:
        if not isinstance(os_name, str):
            return None
        value = os_name.strip().casefold()
        if not value:
            return None
        if "windows" in value or value.startswith("win"):
            return "windows"
        if "macos" in value or "mac os" in value or "darwin" in value or "os x" in value:
            return "macos"
        if "linux" in value or "ubuntu" in value or "debian" in value:
            return "linux"
        return None

    def find_likely_duplicate_device_pairs(self, actor_id: int, owner_id: int) -> list[tuple[str, str]] | None:
        """Suggest same-hostname, same-platform records for owner comparison; never merge them."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._lock:
            devices = list(self._devices.items())[:500]
        groups: dict[tuple[str, str], list[str]] = {}
        for device_id, info in devices:
            if not isinstance(device_id, str) or not device_id or len(device_id) > 128 or not isinstance(info, dict):
                continue
            hostname = info.get("hostname")
            platform = self._duplicate_platform(info.get("os"))
            if not isinstance(hostname, str) or len(hostname) > 255 or platform is None:
                continue
            normalized_host = hostname.strip().rstrip(".").casefold()
            if (not normalized_host or len(normalized_host) > 253
                    or any(unicodedata.category(char) == "Cc" for char in normalized_host)):
                continue
            groups.setdefault((platform, normalized_host), []).append(device_id)
        pairs = []
        for members in groups.values():
            ordered = sorted(set(members), key=lambda value: value.casefold())
            if len(ordered) < 2:
                continue
            pairs.extend((ordered[0], other) for other in ordered[1:])
        return sorted(pairs, key=lambda pair: (pair[0].casefold(), pair[1].casefold()))[:500]

    def all(self) -> dict:
        with self._lock:
            return dict(self._devices)

    def get(self, device_id: str) -> dict | None:
        with self._lock:
            return self._devices.get(device_id)

    def rename(self, device_id: str, name: str) -> bool:
        """Переименовать устройство (отображаемое имя в боте)."""
        with self._lock:
            if device_id not in self._devices:
                return False
            self._devices[device_id]["name"] = name
            self._save()
            return True

    def remove(self, device_id: str) -> bool:
        """Забыть устройство — удалить из хранилища."""
        with self._lock:
            if device_id not in self._devices:
                return False
            del self._devices[device_id]
            self._save()
            with self._catalog_lock:
                pairs = self._normalize_device_relations(self._catalog.get("device_relations", []))
                remaining = {pair for pair in pairs if device_id not in pair}
                if remaining != pairs:
                    self._catalog["device_relations"] = [list(value) for value in sorted(remaining)]
                    self._save_catalog()
            return True

    def archive_device(self, actor_id: int, owner_id: int, device_id: str, *, now: int | None = None) -> bool:
        """Hide one existing device from normal catalog views without deleting it or messaging its agent."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(device_id)
        if not key or len(key) > 128 or self.get(key) is None:
            return False
        archived_at = int(time.time()) if now is None else now
        if type(archived_at) is not int or archived_at < 0:
            raise ValueError("archive timestamp must be a non-negative integer")
        with self._catalog_lock:
            rows = self._catalog["archived_devices"]
            if key in rows:
                return False
            rows[key] = {"archived_at": archived_at}
            self._save_catalog()
        return True

    def get_device_archive(self, actor_id: int, owner_id: int, device_id: str) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            row = self._catalog["archived_devices"].get(str(device_id))
            if not isinstance(row, dict) or type(row.get("archived_at")) is not int or row["archived_at"] < 0:
                return None
            return {"archived_at": row["archived_at"]}

    def list_archived_devices(self, actor_id: int, owner_id: int, *, limit: int = 100) -> list[tuple[str, dict]]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("archive list limit must be between 1 and 500")
        with self._catalog_lock:
            rows = [
                (device_id, row["archived_at"])
                for device_id, row in self._catalog["archived_devices"].items()
                if isinstance(device_id, str) and isinstance(row, dict)
                and type(row.get("archived_at")) is int and row["archived_at"] >= 0
            ]
        with self._lock:
            devices = {device_id: dict(self._devices[device_id]) for device_id, _ in rows if device_id in self._devices}
        return [
            (device_id, devices[device_id])
            for device_id, _timestamp in sorted(rows, key=lambda item: (item[1], item[0].casefold()), reverse=True)
            if device_id in devices
        ][:limit]

    def restore_device(self, actor_id: int, owner_id: int, device_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(device_id)
        if not key or self.get(key) is None:
            return False
        with self._catalog_lock:
            row = self._catalog["archived_devices"].get(key)
            if not isinstance(row, dict) or type(row.get("archived_at")) is not int or row["archived_at"] < 0:
                return False
            del self._catalog["archived_devices"][key]
            self._save_catalog()
        return True

    def get_favorites(self, device_id: str) -> list:
        """Список избранных команд устройства (кнопки быстрого доступа)."""
        with self._lock:
            info = self._devices.get(device_id) or {}
            return list(info.get("favorites", []))

    def toggle_favorite(self, device_id: str, action: str) -> bool:
        """Добавить/убрать команду из избранного. True — если добавлена."""
        with self._lock:
            info = self._devices.get(device_id)
            if info is None:
                return False
            favs = list(info.get("favorites", []))
            if action in favs:
                favs.remove(action)
                added = False
            else:
                favs.append(action)
                added = True
            info["favorites"] = favs
            self._save()
            return added

    def clear(self) -> None:
        """Полностью очистить список сохраненных устройств."""
        with self._lock:
            self._devices.clear()
            self._save()
    def save_selected_snapshot(
        self, actor_id: int, owner_id: int, name: str, device_ids: list[str], *,
        now: int | None = None, ttl_seconds: int = 86_400,
    ) -> dict | None:
        """Save an exact owner-selected device-ID set with a bounded lifetime."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        if int(actor_id) <= 0:
            raise ValueError("actor_id must be a positive Telegram ID")
        label = " ".join(str(name).split())[:80]
        if not label:
            raise ValueError("snapshot name must not be empty")
        if not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 90 * 86_400:
            raise ValueError("snapshot TTL must be between one minute and 90 days")
        selected = list(dict.fromkeys(str(item) for item in device_ids if str(item)))
        if not selected:
            raise ValueError("at least one device must be selected")
        with self._lock:
            missing = [item for item in selected if item not in self._devices]
        if missing:
            raise ValueError("snapshot contains unknown device IDs")
        created = int(now if now is not None else time.time())
        record = {
            "snapshot_id": uuid.uuid4().hex,
            "owner_id": int(actor_id),
            "name": label,
            "device_ids": selected,
            "created_at": created,
            "expires_at": created + ttl_seconds,
        }
        with self._catalog_lock:
            self._catalog["snapshots"][record["snapshot_id"]] = record
            self._save_catalog()
        return dict(record)

    def get_selected_snapshot(
        self, snapshot_id: str, *, actor_id: int, owner_id: int, now: int | None = None,
    ) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        current = int(now if now is not None else time.time())
        with self._catalog_lock:
            record = self._catalog["snapshots"].get(str(snapshot_id))
            if not isinstance(record, dict):
                return None
            if current >= int(record.get("expires_at", 0)):
                del self._catalog["snapshots"][str(snapshot_id)]
                self._save_catalog()
                return None
            saved_ids = list(record.get("device_ids", []))
        with self._lock:
            live = set(self._devices)
        result = dict(record)
        result["device_ids"] = [item for item in saved_ids if item in live]
        result["stale_device_ids"] = [item for item in saved_ids if item not in live]
        return result

    def list_selected_snapshots(
        self, *, actor_id: int, owner_id: int, now: int | None = None, limit: int = 50,
    ) -> list[dict]:
        """List the owner's live saved device sets without exposing them to other users."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("snapshot limit must be between 1 and 100")
        current = int(now if now is not None else time.time())
        with self._catalog_lock:
            ids = [
                str(item.get("snapshot_id"))
                for item in self._catalog["snapshots"].values()
                if isinstance(item, dict) and item.get("owner_id") == int(owner_id)
            ]
        rows = []
        for snapshot_id in ids:
            item = self.get_selected_snapshot(
                snapshot_id, actor_id=actor_id, owner_id=owner_id, now=current,
            )
            if item is not None:
                rows.append(item)
        rows.sort(key=lambda item: (int(item.get("created_at", 0)), item.get("snapshot_id", "")), reverse=True)
        return rows[:limit]

    def delete_selected_snapshot(self, actor_id: int, owner_id: int, snapshot_id: str) -> bool:
        """Delete one saved-set record; this never changes the device inventory."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return False
        key = str(snapshot_id)
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            return False
        with self._catalog_lock:
            record = self._catalog["snapshots"].get(key)
            if not isinstance(record, dict) or record.get("owner_id") != int(owner_id):
                return False
            del self._catalog["snapshots"][key]
            self._save_catalog()
        return True

    def define_typed_field(
        self, actor_id: int, owner_id: int, key: str, field_type: str,
        *, choices: list[str] | None = None,
    ) -> bool:
        """Define owner-only typed inventory metadata; values never go to agents."""
        if int(actor_id) != int(owner_id):
            return False
        field_key = str(key).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", field_key):
            raise ValueError("field key must be 2-31 lowercase ASCII characters")
        if field_type not in {"enum", "date", "boolean"}:
            raise ValueError("field type must be enum, date, or boolean")
        enum_choices: list[str] = []
        if field_type == "enum":
            enum_choices = list(dict.fromkeys(str(value).strip() for value in (choices or [])))
            if not 2 <= len(enum_choices) <= 50 or any(not value or len(value) > 80 for value in enum_choices):
                raise ValueError("enum fields require 2-50 non-empty choices of at most 80 characters")
        elif choices:
            raise ValueError("choices are supported only for enum fields")
        schema = {"type": field_type, "choices": enum_choices}
        with self._catalog_lock:
            current = self._catalog["field_schemas"].get(field_key)
            if current is not None and current != schema:
                raise ValueError("field key already has a different schema")
            self._catalog["field_schemas"][field_key] = schema
            self._save_catalog()
        return True

    def set_typed_field(
        self, actor_id: int, owner_id: int, device_id: str, key: str, value,
    ) -> bool:
        if int(actor_id) != int(owner_id):
            return False
        if self.get(str(device_id)) is None:
            return False
        with self._catalog_lock:
            schema = self._catalog["field_schemas"].get(str(key))
            if not isinstance(schema, dict):
                return False
            kind = schema.get("type")
            if kind == "enum":
                if not isinstance(value, str) or value not in schema.get("choices", []):
                    raise ValueError("value is not one of the declared enum choices")
            elif kind == "date":
                if not isinstance(value, str):
                    raise ValueError("date value must use YYYY-MM-DD")
                try:
                    parsed = date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("date value must use YYYY-MM-DD") from exc
                if parsed.isoformat() != value:
                    raise ValueError("date value must use YYYY-MM-DD")
            elif kind == "boolean":
                if not isinstance(value, bool):
                    raise ValueError("boolean value must be a JSON boolean")
            else:
                raise ValueError("unknown typed field schema")
            values = self._catalog["typed_values"].setdefault(str(device_id), {})
            values[str(key)] = value
            self._save_catalog()
        return True

    def typed_fields(self, actor_id: int, owner_id: int, device_id: str) -> dict | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            return dict(self._catalog["typed_values"].get(str(device_id), {}))

    def typed_field_schemas(self, actor_id: int, owner_id: int) -> dict | None:
        """Return detached, validated typed-field definitions to the owner only."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        with self._catalog_lock:
            raw = self._catalog.get("field_schemas", {})
            if not isinstance(raw, dict):
                return {}
            schemas = {}
            for key, schema in raw.items():
                if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", key):
                    continue
                if not isinstance(schema, dict) or schema.get("type") not in {"enum", "date", "boolean"}:
                    continue
                choices = schema.get("choices", [])
                if schema["type"] == "enum":
                    if (not isinstance(choices, list) or not 2 <= len(choices) <= 50
                            or any(not isinstance(item, str) or not item or len(item) > 80 for item in choices)):
                        continue
                elif choices != []:
                    continue
                schemas[key] = {"type": schema["type"], "choices": list(choices)}
            return json.loads(json.dumps(schemas, ensure_ascii=False))

    @staticmethod
    def _platform(info: dict) -> str:
        value = str(info.get("os") or "unknown").lower()
        if "mac" in value or "darwin" in value:
            return "macos"
        if "win" in value:
            return "windows"
        if "linux" in value:
            return "linux"
        return "unknown"

    def capture_inactivity_snapshot(self, *, now: int | None = None) -> dict:
        current = int(now if now is not None else time.time())
        today = datetime.fromtimestamp(current, tz=timezone.utc).date().isoformat()
        bucket_names = ("under_7_days", "7_to_30_days", "over_30_days", "unknown")
        counts: dict[str, dict[str, int]] = {}
        with self._lock:
            devices = [dict(info) for info in self._devices.values()]
            device_ids = list(self._devices)
        with self._catalog_lock:
            maintenance = json.loads(json.dumps(self._catalog["device_maintenance"]))
        for device_id, info in zip(device_ids, devices):
            row = maintenance.get(device_id)
            try:
                until_date = self._normalize_device_review_date(row.get("until_date")) if isinstance(row, dict) else ""
            except ValueError:
                until_date = ""
            if (until_date and until_date >= today and row.get("exclude_inactivity") is True):
                continue
            platform = self._platform(info)
            groups = counts.setdefault(platform, {name: 0 for name in bucket_names})
            try:
                age = current - int(info.get("last_seen"))
            except (TypeError, ValueError):
                age = -1
            bucket = (
                "unknown" if age < 0
                else "under_7_days" if age < 7 * 86_400
                else "7_to_30_days" if age < 30 * 86_400
                else "over_30_days"
            )
            groups[bucket] += 1
        record = {
            "snapshot_id": uuid.uuid4().hex,
            "captured_at": current,
            "counts": counts,
        }
        with self._catalog_lock:
            history = self._catalog["inactivity_snapshots"]
            history.append(record)
            self._catalog["inactivity_snapshots"] = history[-200:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_inactivity_snapshots(self, *, limit: int = 20) -> list[dict]:
        if not 1 <= int(limit) <= 200:
            raise ValueError("snapshot limit must be between 1 and 200")
        with self._catalog_lock:
            rows = [
                (sequence, json.loads(json.dumps(item)))
                for sequence, item in enumerate(self._catalog["inactivity_snapshots"])
                if self._valid_inactivity_snapshot(item)
            ]
        # The append position is the authoritative tie-breaker: UUID order is
        # random and timestamps have only second precision.
        rows.sort(key=lambda pair: (pair[1]["captured_at"], pair[0]), reverse=True)
        return [item for _, item in rows[:int(limit)]]

    @staticmethod
    def _valid_inactivity_snapshot(item) -> bool:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("snapshot_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", item["snapshot_id"]):
            return False
        captured_at = item.get("captured_at")
        if type(captured_at) is not int or captured_at < 0:
            return False
        counts = item.get("counts")
        if not isinstance(counts, dict):
            return False
        buckets = {"under_7_days", "7_to_30_days", "over_30_days", "unknown"}
        platforms = {"windows", "macos", "linux", "unknown"}
        for platform, values in counts.items():
            if platform not in platforms or not isinstance(values, dict) or set(values) != buckets:
                return False
            if any(type(value) is not int or value < 0 for value in values.values()):
                return False
        return True

    def compare_inactivity_snapshots(self, earlier_id: str, later_id: str) -> dict[str, dict[str, int]]:
        by_id = {
            item["snapshot_id"]: item
            for item in self.list_inactivity_snapshots(limit=200)
        }
        earlier, later = by_id.get(str(earlier_id)), by_id.get(str(later_id))
        if earlier is None or later is None:
            raise ValueError("inactivity snapshot not found")
        result: dict[str, dict[str, int]] = {}
        platforms = set(earlier["counts"]) | set(later["counts"])
        for platform in platforms:
            before = earlier["counts"].get(platform, {})
            after = later["counts"].get(platform, {})
            buckets = set(before) | set(after)
            result[platform] = {
                bucket: after.get(bucket, 0) - before.get(bucket, 0)
                for bucket in buckets
            }
        return result

    @staticmethod
    def _normalize_hardware_inventory(data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("hardware inventory must be an object")
        normalized = {}
        for field in ("manufacturer", "model", "cpu", "architecture"):
            value = data.get(field)
            if value is None:
                normalized[field] = None
                continue
            if not isinstance(value, str) or len(value.strip()) > 120:
                raise ValueError("invalid hardware inventory text field")
            value = value.strip()
            if any(ord(char) < 32 for char in value):
                raise ValueError("invalid hardware inventory text field")
            normalized[field] = value or None
        memory = data.get("memory_gib")
        if memory is not None and (type(memory) is not int or not 1 <= memory <= 2048):
            raise ValueError("invalid hardware inventory memory size")
        normalized["memory_gib"] = memory
        graphics = data.get("graphics", [])
        if not isinstance(graphics, list) or len(graphics) > 8:
            raise ValueError("invalid hardware inventory graphics list")
        normalized_graphics = []
        for value in graphics:
            if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 120
                    or any(ord(char) < 32 for char in value)):
                raise ValueError("invalid hardware inventory graphics entry")
            if value.strip() not in normalized_graphics:
                normalized_graphics.append(value.strip())
        normalized["graphics"] = sorted(normalized_graphics)
        if not any(value is not None for key, value in normalized.items() if key != "graphics") and not normalized_graphics:
            raise ValueError("hardware inventory contains no observed fields")
        return normalized

    def save_hardware_inventory_snapshot(
        self, device_id: str, data: dict, *, now: int | None = None,
    ) -> dict:
        device_id = str(device_id)
        if not device_id or len(device_id) > 128:
            raise ValueError("invalid hardware inventory device id")
        inventory = self._normalize_hardware_inventory(data)
        captured_at = int(now if now is not None else time.time())
        if captured_at < 0:
            raise ValueError("invalid hardware inventory timestamp")
        record = {
            "snapshot_id": uuid.uuid4().hex,
            "device_id": device_id,
            "captured_at": captured_at,
            "inventory": inventory,
        }
        with self._catalog_lock:
            history = self._catalog["hardware_snapshots"].setdefault(device_id, [])
            if not isinstance(history, list):
                history = []
            history.append(record)
            self._catalog["hardware_snapshots"][device_id] = history[-50:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_hardware_inventory_snapshots(self, device_id: str, *, limit: int = 10) -> list[dict]:
        if not 1 <= int(limit) <= 50:
            raise ValueError("hardware inventory snapshot limit must be between 1 and 50")
        with self._catalog_lock:
            rows = self._catalog["hardware_snapshots"].get(str(device_id), [])
            valid = [
                json.loads(json.dumps(row)) for row in rows
                if self._valid_hardware_inventory_snapshot(row, expected_device_id=str(device_id))
            ] if isinstance(rows, list) else []
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    @classmethod
    def _valid_hardware_inventory_snapshot(cls, row, *, expected_device_id: str | None = None) -> bool:
        if not isinstance(row, dict):
            return False
        if not isinstance(row.get("snapshot_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"]):
            return False
        if expected_device_id is not None and row.get("device_id") != expected_device_id:
            return False
        if not isinstance(row.get("device_id"), str) or not 1 <= len(row["device_id"]) <= 128:
            return False
        if type(row.get("captured_at")) is not int or row["captured_at"] < 0:
            return False
        try:
            return cls._normalize_hardware_inventory(row.get("inventory")) == row.get("inventory")
        except ValueError:
            return False

    def compare_hardware_inventory_snapshots(self, device_id: str) -> dict:
        snapshots = self.list_hardware_inventory_snapshots(device_id, limit=2)
        if len(snapshots) < 2:
            raise ValueError("at least two hardware inventory snapshots are required")
        earlier, later = snapshots[1], snapshots[0]
        before, after = earlier["inventory"], later["inventory"]
        changes = [
            {"field": field, "before": before.get(field), "after": after.get(field)}
            for field in sorted(set(before) | set(after))
            if before.get(field) != after.get(field)
        ]
        return {"before": earlier, "after": later, "changes": changes}

    @staticmethod
    def _normalize_smart_trend_disks(disks: list) -> list[dict]:
        allowed = {
            "temperature_c": (0, 150), "wear_percent": (0, 100),
            "power_on_hours": (0, 1e12), "read_errors_total": (0, 1e15),
            "write_errors_total": (0, 1e15),
        }
        if not isinstance(disks, list) or len(disks) > 32:
            raise ValueError("SMART trend must contain at most 32 disks")
        result, labels = [], set()
        for disk in disks:
            if not isinstance(disk, dict):
                raise ValueError("SMART trend disk must be an object")
            label, counters = disk.get("label"), disk.get("counters")
            if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 160
                    or any(ord(char) < 32 for char in label)):
                raise ValueError("invalid SMART trend disk label")
            label = " ".join(label.split())
            if label in labels:
                raise ValueError("duplicate SMART trend disk label")
            labels.add(label)
            if not isinstance(counters, dict) or len(counters) > len(allowed):
                raise ValueError("invalid SMART trend counters")
            clean = {}
            for name, value in counters.items():
                bounds = allowed.get(name)
                if (bounds is None or isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not bounds[0] <= value <= bounds[1]):
                    raise ValueError("invalid SMART trend counter")
                clean[name] = float(value)
            if clean:
                result.append({"label": label, "counters": dict(sorted(clean.items()))})
        return sorted(result, key=lambda row: row["label"])

    def save_smart_trend_snapshot(self, device_id: str, disks: list, *, now: int | None = None) -> dict:
        device_id = str(device_id)
        if not 1 <= len(device_id) <= 128:
            raise ValueError("invalid SMART trend device id")
        clean = self._normalize_smart_trend_disks(disks)
        captured_at = int(now if now is not None else time.time())
        if captured_at < 0 or not clean:
            raise ValueError("SMART trend snapshot needs supported counters and a valid timestamp")
        record = {"snapshot_id": uuid.uuid4().hex, "device_id": device_id,
                  "captured_at": captured_at, "disks": clean}
        with self._catalog_lock:
            history = self._catalog["smart_trend_snapshots"].setdefault(device_id, [])
            if not isinstance(history, list):
                history = []
            history.append(record)
            self._catalog["smart_trend_snapshots"][device_id] = history[-50:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_smart_trend_snapshots(self, device_id: str, *, limit: int = 10) -> list[dict]:
        if not 1 <= int(limit) <= 50:
            raise ValueError("SMART trend limit must be between 1 and 50")
        with self._catalog_lock:
            rows = self._catalog["smart_trend_snapshots"].get(str(device_id), [])
            valid = [json.loads(json.dumps(row)) for row in rows
                     if self._valid_smart_trend_snapshot(row, expected_device_id=str(device_id))] if isinstance(rows, list) else []
        # Python's stable reverse sort retains append order when timestamps collide.
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    @staticmethod
    def _normalize_wifi_quality_sample(sample: dict) -> dict:
        if not isinstance(sample, dict) or set(sample) != {"captured_at", "source", "metric", "value"}:
            raise ValueError("invalid Wi-Fi quality sample fields")
        captured_at, source = sample.get("captured_at"), sample.get("source")
        metric, value = sample.get("metric"), sample.get("value")
        if type(captured_at) is not int or captured_at < 0:
            raise ValueError("invalid Wi-Fi quality sample time")
        bounds = {
            ("windows_netsh_signal_percent", "signal_percent"): (0, 100),
            ("macos_ipconfig_agrctlrssi", "rssi_dbm"): (-120, 0),
        }
        allowed = bounds.get((source, metric))
        if allowed is None or type(value) is not int or not allowed[0] <= value <= allowed[1]:
            raise ValueError("invalid Wi-Fi quality metric")
        return {"captured_at": captured_at, "source": source, "metric": metric, "value": value}

    def record_wifi_quality_sample(self, actor_id: int, owner_id: int, device_id: str, sample: dict) -> dict | None:
        """Persist one explicit owner-requested signal sample; never stores SSID or credentials."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        device = str(device_id)
        if not 1 <= len(device) <= 128:
            raise ValueError("invalid Wi-Fi quality device id")
        clean = self._normalize_wifi_quality_sample(sample)
        with self._catalog_lock:
            histories = self._catalog["wifi_quality_histories"]
            rows = histories.get(device, [])
            if not isinstance(rows, list):
                rows = []
            rows = [self._normalize_wifi_quality_sample(row) for row in rows[-59:]]
            rows.append(clean)
            histories[device] = rows
            self._save_catalog()
        return dict(clean)

    def list_wifi_quality_samples(self, actor_id: int, owner_id: int, device_id: str, *, limit: int = 60) -> list[dict] | None:
        """Return the owner's bounded, identity-free signal history in capture order."""
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
        if type(limit) is not int or not 1 <= limit <= 60:
            raise ValueError("Wi-Fi quality history limit must be between 1 and 60")
        device = str(device_id)
        if not 1 <= len(device) <= 128:
            raise ValueError("invalid Wi-Fi quality device id")
        with self._catalog_lock:
            rows = self._catalog["wifi_quality_histories"].get(device, [])
            valid = []
            if isinstance(rows, list):
                for row in rows:
                    try:
                        valid.append(self._normalize_wifi_quality_sample(row))
                    except ValueError:
                        continue
        return [dict(row) for row in valid[-limit:]]

    @classmethod
    def _valid_smart_trend_snapshot(cls, row, *, expected_device_id: str | None = None) -> bool:
        if (not isinstance(row, dict) or not isinstance(row.get("snapshot_id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"])
                or not isinstance(row.get("device_id"), str)
                or expected_device_id is not None and row["device_id"] != expected_device_id
                or type(row.get("captured_at")) is not int or row["captured_at"] < 0):
            return False
        try:
            return bool(cls._normalize_smart_trend_disks(row.get("disks"))) and cls._normalize_smart_trend_disks(row["disks"]) == row["disks"]
        except ValueError:
            return False

    def compare_smart_trend_snapshots(self, device_id: str) -> dict:
        rows = self.list_smart_trend_snapshots(device_id, limit=2)
        if len(rows) < 2:
            raise ValueError("at least two SMART trend snapshots are required")
        before, after = rows[1], rows[0]
        before_map = {disk["label"]: disk["counters"] for disk in before["disks"]}
        after_map = {disk["label"]: disk["counters"] for disk in after["disks"]}
        changes = []
        for label in sorted(set(before_map) | set(after_map)):
            old, new = before_map.get(label, {}), after_map.get(label, {})
            for counter in sorted(set(old) | set(new)):
                previous, current = old.get(counter), new.get(counter)
                if previous != current:
                    changes.append({"label": label, "counter": counter, "before": previous,
                                    "after": current, "delta": current - previous if previous is not None and current is not None else None})
        return {"before": before, "after": after,
                "elapsed_seconds": max(0, after["captured_at"] - before["captured_at"]),
                "changes": changes}

    @staticmethod
    def _normalize_system_summary(summary: dict) -> dict:
        if not isinstance(summary, dict):
            raise ValueError("system summary must be an object")
        raw_platform = summary.get("platform")
        if not isinstance(raw_platform, str) or len(raw_platform) > 80:
            raise ValueError("invalid system summary platform")
        lowered = raw_platform.strip().lower()
        platform = "Windows" if lowered.startswith("win") else "macOS" if lowered.startswith(("mac", "darwin")) else None
        if platform is None:
            raise ValueError("unsupported system summary platform")
        clean = {"platform": platform}
        observed = 0
        for field in ("cpu_percent", "ram_percent", "disk_percent"):
            value = summary.get(field)
            if value is None:
                clean[field] = None
                continue
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 100):
                raise ValueError(f"invalid system summary {field}")
            clean[field] = round(float(value), 1)
            observed += 1
        if not observed:
            raise ValueError("system summary has no measured resource values")
        return clean

    def save_system_summary_snapshot(
        self, device_id: str, summary: dict, *, captured_at: int, now: int | None = None,
        capture_type: str = "on_demand",
    ) -> dict:
        device_id = str(device_id)
        if not 1 <= len(device_id) <= 128:
            raise ValueError("invalid system summary device id")
        if not isinstance(capture_type, str) or capture_type not in {"on_demand", "scheduled"}:
            raise ValueError("invalid system summary capture type")
        clean = self._normalize_system_summary(summary)
        current = int(now if now is not None else time.time())
        if type(captured_at) is not int or captured_at < current - self.SYSTEM_SUMMARY_TTL_SECONDS or captured_at > current + 300:
            raise ValueError("system summary timestamp is outside the accepted retention window")
        record = {"snapshot_id": uuid.uuid4().hex, "device_id": device_id,
                  "capture_type": capture_type,
                  "captured_at": captured_at, "summary": clean}
        with self._catalog_lock:
            history = self._catalog["system_summary_snapshots"].get(device_id, [])
            if not isinstance(history, list):
                history = []
            retained = [row for row in history if self._valid_system_summary_snapshot(
                row, expected_device_id=device_id, now=current,
            )]
            retained.append(record)
            self._catalog["system_summary_snapshots"][device_id] = retained[-self.SYSTEM_SUMMARY_LIMIT:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def get_system_summary_schedule(self, device_id: str) -> dict:
        key = str(device_id)
        with self._catalog_lock:
            row = self._catalog["system_summary_schedules"].get(key)
            if not self._valid_system_summary_schedule(row, expected_device_id=key):
                return {"interval_seconds": 0, "schedule_id": None, "next_sample_at": None}
            return {field: row[field] for field in ("interval_seconds", "schedule_id", "next_sample_at")}

    def save_system_summary_schedule(
        self, device_id: str, *, interval_seconds: int, schedule_id: str,
        next_sample_at: int | None, now: int | None = None,
    ) -> dict:
        device_id = str(device_id)
        if not 1 <= len(device_id) <= 128:
            raise ValueError("invalid system summary schedule device id")
        if type(interval_seconds) is not int or interval_seconds not in {0, 900, 1800, 3600}:
            raise ValueError("unsupported system summary schedule interval")
        if not isinstance(schedule_id, str) or not re.fullmatch(r"[0-9a-f]{32}", schedule_id):
            raise ValueError("invalid system summary schedule id")
        if interval_seconds == 0:
            if next_sample_at is not None:
                raise ValueError("disabled schedule cannot have a next sample")
        elif type(next_sample_at) is not int:
            raise ValueError("enabled schedule requires a next sample time")
        current = int(now if now is not None else time.time())
        if next_sample_at is not None and not current <= next_sample_at <= current + 3600 + 300:
            raise ValueError("system summary schedule time is outside the accepted window")
        row = {"device_id": device_id, "interval_seconds": interval_seconds,
               "schedule_id": schedule_id, "next_sample_at": next_sample_at}
        with self._catalog_lock:
            self._catalog["system_summary_schedules"][device_id] = row
            self._save_catalog()
        return {field: row[field] for field in ("interval_seconds", "schedule_id", "next_sample_at")}

    def save_scheduled_system_summary_snapshot(
        self, device_id: str, *, schedule_id: str, summary: dict, captured_at: int,
        now: int | None = None,
    ) -> dict | None:
        schedule = self.get_system_summary_schedule(device_id)
        if (schedule["interval_seconds"] == 0 or schedule["schedule_id"] != schedule_id
                or not isinstance(schedule_id, str)):
            return None
        return self.save_system_summary_snapshot(
            device_id, summary, captured_at=captured_at, now=now, capture_type="scheduled",
        )

    def aggregate_scheduled_system_summary(
        self, device_id: str, *, period_seconds: int, now: int | None = None,
    ) -> dict:
        """Summarize retained, explicitly scheduled samples; never infer missing values."""
        if type(period_seconds) is not int or period_seconds not in self.SYSTEM_SUMMARY_PERIODS:
            raise ValueError("unsupported system summary period")
        current = int(now if now is not None else time.time())
        start = current - period_seconds
        scheduled = [
            row for row in self.list_system_summary_snapshots(
                device_id, limit=self.SYSTEM_SUMMARY_LIMIT, now=current,
            )
            if row.get("capture_type") == "scheduled" and start <= row["captured_at"] <= current
        ]
        if not scheduled:
            raise ValueError("no fresh scheduled system summaries")
        metrics = {}
        for field in ("cpu_percent", "ram_percent", "disk_percent"):
            values = [row["summary"][field] for row in scheduled if row["summary"].get(field) is not None]
            metrics[field] = (
                {
                    "count": len(values),
                    "min": min(values),
                    "mean": round(sum(values) / len(values), 1),
                    "max": max(values),
                }
                if values else None
            )
        return {
            "period_seconds": period_seconds,
            "start_at": start,
            "end_at": current,
            "sample_count": len(scheduled),
            "metrics": metrics,
        }

    @staticmethod
    def _default_system_threshold_record(device_id: str) -> dict:
        return {
            "device_id": device_id,
            "thresholds": {"cpu_percent": None, "ram_percent": None, "disk_percent": None, "temperature_celsius": None},
            "monitor": {
                key: {"streak": 0, "latched": False, "last_sample_at": None}
                for key in ("cpu_percent", "ram_percent", "disk_percent", "temperature_celsius")
            },
        }

    def get_system_summary_thresholds(self, device_id: str) -> dict:
        key = str(device_id)
        with self._catalog_lock:
            row = self._catalog["system_summary_thresholds"].get(key)
            if not self._valid_system_summary_threshold_record(row, expected_device_id=key):
                row = self._default_system_threshold_record(key)
            return dict(row["thresholds"])

    def set_system_summary_threshold(self, device_id: str, metric: str, threshold: int | None) -> dict:
        device_id = str(device_id)
        if not 1 <= len(device_id) <= 128:
            raise ValueError("invalid system threshold device id")
        choices = {
            "cpu_percent": {70, 75, 80, 85, 90, 95},
            "ram_percent": {70, 75, 80, 85, 90, 95},
            "disk_percent": {70, 75, 80, 85, 90, 95},
            "temperature_celsius": {65, 70, 75, 80, 85, 90},
        }
        if metric not in choices:
            raise ValueError("unsupported system threshold metric")
        if threshold is not None and (type(threshold) is not int or threshold not in choices[metric]):
            raise ValueError("unsupported system threshold value")
        with self._catalog_lock:
            old = self._catalog["system_summary_thresholds"].get(device_id)
            row = json.loads(json.dumps(old)) if self._valid_system_summary_threshold_record(old, expected_device_id=device_id) else self._default_system_threshold_record(device_id)
            row["thresholds"][metric] = None if threshold is None else float(threshold)
            row["monitor"][metric] = {"streak": 0, "latched": False, "last_sample_at": None}
            self._catalog["system_summary_thresholds"][device_id] = row
            self._save_catalog()
            return dict(row["thresholds"])

    def evaluate_system_summary_thresholds(
        self, device_id: str, *, schedule_id: str, summary: dict, now: int | None = None,
    ) -> list[dict]:
        """Alert only after two consecutive, fresh samples in the active schedule."""
        device_id = str(device_id)
        sampled_at = int(time.time() if now is None else now)
        schedule = self.get_system_summary_schedule(device_id)
        if schedule["interval_seconds"] == 0 or schedule["schedule_id"] != schedule_id:
            return []
        values: dict[str, float | None] = {}
        for metric in ("cpu_percent", "ram_percent", "disk_percent"):
            value = summary.get(metric) if isinstance(summary, dict) else None
            values[metric] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100 else None
        temperature = summary.get("temperature_celsius") if isinstance(summary, dict) else None
        values["temperature_celsius"] = (float(temperature) if isinstance(temperature, (int, float))
                                            and not isinstance(temperature, bool) and math.isfinite(temperature)
                                            and -40 <= temperature <= 150 else None)
        alerts = []
        with self._catalog_lock:
            old = self._catalog["system_summary_thresholds"].get(device_id)
            row = json.loads(json.dumps(old)) if self._valid_system_summary_threshold_record(old, expected_device_id=device_id) else self._default_system_threshold_record(device_id)
            for metric, threshold in row["thresholds"].items():
                monitor = row["monitor"][metric]
                value = values[metric]
                previous_at = monitor["last_sample_at"]
                if type(previous_at) is int and sampled_at <= previous_at:
                    continue
                if type(previous_at) is int and sampled_at - previous_at > schedule["interval_seconds"] * 2 + 300:
                    monitor["streak"] = 0
                monitor["last_sample_at"] = sampled_at
                if threshold is None or value is None:
                    monitor["streak"] = 0
                    continue
                hysteresis = 3.0 if metric == "temperature_celsius" else 5.0
                if value <= threshold - hysteresis:
                    monitor["streak"] = 0
                    monitor["latched"] = False
                elif value < threshold:
                    monitor["streak"] = 0
                else:
                    monitor["streak"] = min(2, monitor["streak"] + 1)
                    if monitor["streak"] >= 2 and not monitor["latched"]:
                        alerts.append({"metric": metric, "threshold": threshold, "observed": round(value, 1)})
                        monitor["latched"] = True
            self._catalog["system_summary_thresholds"][device_id] = row
            self._save_catalog()
        return alerts

    @classmethod
    def _valid_system_summary_threshold_record(cls, row, *, expected_device_id: str | None = None) -> bool:
        metrics = {"cpu_percent", "ram_percent", "disk_percent", "temperature_celsius"}
        if (not isinstance(row, dict) or set(row) != {"device_id", "thresholds", "monitor"}
                or not isinstance(row.get("device_id"), str) or not 1 <= len(row["device_id"]) <= 128
                or expected_device_id is not None and row["device_id"] != expected_device_id
                or not isinstance(row.get("thresholds"), dict) or set(row["thresholds"]) != metrics
                or not isinstance(row.get("monitor"), dict) or set(row["monitor"]) != metrics):
            return False
        for metric, threshold in row["thresholds"].items():
            allowed = {65, 70, 75, 80, 85, 90} if metric == "temperature_celsius" else {70, 75, 80, 85, 90, 95}
            if threshold is not None and (type(threshold) not in {int, float} or threshold not in allowed):
                return False
            state = row["monitor"][metric]
            if (not isinstance(state, dict) or set(state) != {"streak", "latched", "last_sample_at"}
                    or type(state.get("streak")) is not int or state["streak"] not in {0, 1, 2}
                    or type(state.get("latched")) is not bool
                    or state.get("last_sample_at") is not None and type(state.get("last_sample_at")) is not int):
                return False
        return True

    @staticmethod
    def _valid_system_summary_schedule(row, *, expected_device_id: str | None = None) -> bool:
        if (not isinstance(row, dict)
                or set(row) != {"device_id", "interval_seconds", "schedule_id", "next_sample_at"}
                or not isinstance(row.get("device_id"), str)
                or not 1 <= len(row["device_id"]) <= 128
                or expected_device_id is not None and row["device_id"] != expected_device_id
                or type(row.get("interval_seconds")) is not int
                or row["interval_seconds"] not in {0, 900, 1800, 3600}
                or not isinstance(row.get("schedule_id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", row["schedule_id"])):
            return False
        if row["interval_seconds"] == 0:
            return row["next_sample_at"] is None
        return type(row.get("next_sample_at")) is int and row["next_sample_at"] >= 0

    def list_system_summary_snapshots(
        self, device_id: str, *, limit: int = 10, now: int | None = None,
    ) -> list[dict]:
        if not 1 <= int(limit) <= self.SYSTEM_SUMMARY_LIMIT:
            raise ValueError("system summary history limit must be between 1 and 50")
        current = int(now if now is not None else time.time())
        key = str(device_id)
        with self._catalog_lock:
            history = self._catalog["system_summary_snapshots"].get(key, [])
            valid = [json.loads(json.dumps(row)) for row in history
                     if self._valid_system_summary_snapshot(row, expected_device_id=key, now=current)] if isinstance(history, list) else []
            if isinstance(history, list) and len(valid) != len(history):
                self._catalog["system_summary_snapshots"][key] = valid[-self.SYSTEM_SUMMARY_LIMIT:]
                self._save_catalog()
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    def compare_system_summary_snapshots(self, device_id: str, *, now: int | None = None) -> dict:
        snapshots = self.list_system_summary_snapshots(device_id, limit=2, now=now)
        if len(snapshots) < 2:
            raise ValueError("two system summary snapshots are required")
        earlier, later = snapshots[1], snapshots[0]
        before, after = earlier["summary"], later["summary"]
        changes = [
            {"field": field, "before": before.get(field), "after": after.get(field)}
            for field in ("platform", "cpu_percent", "ram_percent", "disk_percent")
            if before.get(field) != after.get(field)
        ]
        return {"before": earlier, "after": later, "changes": changes}

    def get_system_summary_snapshot(
        self, device_id: str, snapshot_id: str, *, now: int | None = None,
    ) -> dict | None:
        if (not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{32}", snapshot_id)
                or not isinstance(device_id, str) or not 1 <= len(device_id) <= 128):
            return None
        rows = self.list_system_summary_snapshots(device_id, limit=self.SYSTEM_SUMMARY_LIMIT, now=now)
        return next((row for row in rows if row["snapshot_id"] == snapshot_id), None)

    @classmethod
    def _valid_system_summary_snapshot(
        cls, row, *, expected_device_id: str | None = None, now: int | None = None,
    ) -> bool:
        if (not isinstance(row, dict)
                or set(row) not in (
                    {"snapshot_id", "device_id", "captured_at", "summary"},
                    {"snapshot_id", "device_id", "capture_type", "captured_at", "summary"},
                )
                or not isinstance(row.get("snapshot_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"])
                or not isinstance(row.get("device_id"), str) or not 1 <= len(row["device_id"]) <= 128
                or expected_device_id is not None and row["device_id"] != expected_device_id
                or type(row.get("captured_at")) is not int):
            return False
        if ("capture_type" in row
                and (not isinstance(row["capture_type"], str)
                     or row["capture_type"] not in {"on_demand", "scheduled"})):
            return False
        current = int(now if now is not None else time.time())
        if not current - cls.SYSTEM_SUMMARY_TTL_SECONDS <= row["captured_at"] <= current + 300:
            return False
        try:
            return cls._normalize_system_summary(row.get("summary")) == row.get("summary")
        except ValueError:
            return False

    @staticmethod
    def _normalize_driver_inventory(rows: list) -> list[dict]:
        if not isinstance(rows, list) or len(rows) > 300:
            raise ValueError("driver inventory must be a bounded list")
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("driver inventory rows must be objects")
            item = {}
            for field in ("device_name", "provider", "version", "driver_date"):
                value = row.get(field)
                if value is None:
                    item[field] = None
                    continue
                if not isinstance(value, str) or len(value.strip()) > 160:
                    raise ValueError("invalid driver inventory text field")
                value = value.strip()
                if any(ord(char) < 32 for char in value):
                    raise ValueError("invalid driver inventory text field")
                item[field] = value or None
            if not item["device_name"]:
                raise ValueError("driver inventory device name is required")
            key = (item["device_name"].casefold(), (item["provider"] or "").casefold())
            if key not in seen:
                seen.add(key)
                result.append(item)
        return sorted(result, key=lambda item: (item["device_name"].casefold(), (item["provider"] or "").casefold()))

    def save_driver_inventory_snapshot(
        self, device_id: str, rows: list, *, now: int | None = None,
    ) -> dict:
        device_id = str(device_id)
        if not device_id or len(device_id) > 128:
            raise ValueError("invalid driver inventory device id")
        inventory = self._normalize_driver_inventory(rows)
        captured_at = int(now if now is not None else time.time())
        if captured_at < 0:
            raise ValueError("invalid driver inventory timestamp")
        record = {
            "snapshot_id": uuid.uuid4().hex, "device_id": device_id,
            "captured_at": captured_at, "drivers": inventory,
        }
        with self._catalog_lock:
            history = self._catalog["driver_snapshots"].setdefault(device_id, [])
            if not isinstance(history, list):
                history = []
            history.append(record)
            self._catalog["driver_snapshots"][device_id] = history[-20:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_driver_inventory_snapshots(self, device_id: str, *, limit: int = 10) -> list[dict]:
        if not 1 <= int(limit) <= 20:
            raise ValueError("driver inventory snapshot limit must be between 1 and 20")
        with self._catalog_lock:
            rows = self._catalog["driver_snapshots"].get(str(device_id), [])
            valid = [json.loads(json.dumps(row)) for row in rows
                     if self._valid_driver_inventory_snapshot(row, expected_device_id=str(device_id))] if isinstance(rows, list) else []
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    @classmethod
    def _valid_driver_inventory_snapshot(cls, row, *, expected_device_id: str | None = None) -> bool:
        if (not isinstance(row, dict)
                or not isinstance(row.get("snapshot_id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"])
                or type(row.get("captured_at")) is not int or row["captured_at"] < 0
                or not isinstance(row.get("device_id"), str) or not 1 <= len(row["device_id"]) <= 128
                or (expected_device_id is not None and row.get("device_id") != expected_device_id)):
            return False
        try:
            return cls._normalize_driver_inventory(row.get("drivers")) == row.get("drivers")
        except ValueError:
            return False

    def compare_driver_inventory_snapshots(self, device_id: str) -> dict:
        snapshots = self.list_driver_inventory_snapshots(device_id, limit=2)
        if len(snapshots) < 2:
            raise ValueError("at least two driver inventory snapshots are required")
        earlier, later = snapshots[1], snapshots[0]
        before = {(item["device_name"], item["provider"]): item for item in earlier["drivers"]}
        after = {(item["device_name"], item["provider"]): item for item in later["drivers"]}
        changes = []
        for key in sorted(set(before) | set(after), key=lambda item: (item[0].casefold(), (item[1] or "").casefold())):
            old, new = before.get(key), after.get(key)
            old_version = {"version": old["version"], "driver_date": old["driver_date"]} if old else None
            new_version = {"version": new["version"], "driver_date": new["driver_date"]} if new else None
            if old_version == new_version:
                continue
            changes.append({
                "kind": "added" if old is None else "removed" if new is None else "changed",
                "device_name": key[0], "provider": key[1], "before": old_version, "after": new_version,
            })
        return {"before": earlier, "after": later, "changes": changes[:300]}

    @staticmethod
    def _normalize_installed_app_inventory(rows: list) -> list[dict]:
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("installed-app inventory must be a bounded list")
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("installed-app rows must be objects")
            item = {}
            for field in ("name", "publisher", "version"):
                value = row.get(field)
                if value is None:
                    item[field] = None
                    continue
                if not isinstance(value, str) or len(value.strip()) > 160:
                    raise ValueError("invalid installed-app text field")
                value = value.strip()
                if any(ord(char) < 32 for char in value):
                    raise ValueError("invalid installed-app text field")
                item[field] = value or None
            if not item["name"]:
                raise ValueError("installed-app name is required")
            key = (item["name"].casefold(), (item["publisher"] or "").casefold())
            if key not in seen:
                seen.add(key)
                result.append(item)
        return sorted(result, key=lambda item: (item["name"].casefold(), (item["publisher"] or "").casefold()))

    def save_installed_app_snapshot(self, device_id: str, rows: list, *, now: int | None = None) -> dict:
        device_id = str(device_id)
        if not device_id or len(device_id) > 128:
            raise ValueError("invalid installed-app device id")
        inventory = self._normalize_installed_app_inventory(rows)
        captured_at = int(now if now is not None else time.time())
        if captured_at < 0:
            raise ValueError("invalid installed-app timestamp")
        record = {"snapshot_id": uuid.uuid4().hex, "device_id": device_id,
                  "captured_at": captured_at, "apps": inventory}
        with self._catalog_lock:
            history = self._catalog["installed_app_snapshots"].setdefault(device_id, [])
            if not isinstance(history, list):
                history = []
            history.append(record)
            self._catalog["installed_app_snapshots"][device_id] = history[-20:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_installed_app_snapshots(self, device_id: str, *, limit: int = 10) -> list[dict]:
        if not 1 <= int(limit) <= 20:
            raise ValueError("installed-app snapshot limit must be between 1 and 20")
        with self._catalog_lock:
            rows = self._catalog["installed_app_snapshots"].get(str(device_id), [])
            valid = []
            if isinstance(rows, list):
                for row in rows:
                    if (isinstance(row, dict) and isinstance(row.get("snapshot_id"), str)
                            and re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"])
                            and row.get("device_id") == str(device_id)
                            and type(row.get("captured_at")) is int and row["captured_at"] >= 0):
                        try:
                            if self._normalize_installed_app_inventory(row.get("apps")) == row.get("apps"):
                                valid.append(json.loads(json.dumps(row)))
                        except ValueError:
                            continue
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    def compare_installed_app_snapshots(self, device_id: str) -> dict:
        snapshots = self.list_installed_app_snapshots(device_id, limit=2)
        if len(snapshots) < 2:
            raise ValueError("at least two installed-app snapshots are required")
        earlier, later = snapshots[1], snapshots[0]
        before = {(item["name"], item["publisher"]): item for item in earlier["apps"]}
        after = {(item["name"], item["publisher"]): item for item in later["apps"]}
        changes = []
        for key in sorted(set(before) | set(after), key=lambda item: (item[0].casefold(), (item[1] or "").casefold())):
            old, new = before.get(key), after.get(key)
            old_version, new_version = (old or {}).get("version"), (new or {}).get("version")
            if old is not None and new is not None and old_version == new_version:
                continue
            changes.append({"kind": "added" if old is None else "removed" if new is None else "changed",
                            "name": key[0], "publisher": key[1], "before": old_version, "after": new_version})
        return {"before": earlier, "after": later, "changes": changes[:500]}

    @staticmethod
    def _normalize_startup_inventory(rows: list) -> list[dict]:
        if not isinstance(rows, list) or len(rows) > 300:
            raise ValueError("startup inventory must be a bounded list")
        scopes = {"user_run", "machine_run", "user_folder", "machine_folder"}
        normalized, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("startup inventory rows must be objects")
            name, scope = row.get("name"), row.get("scope")
            if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 160
                    or any(ord(char) < 32 for char in name) or scope not in scopes):
                raise ValueError("invalid startup inventory entry")
            entry = {"name": name.strip(), "scope": scope}
            key = (entry["name"].casefold(), scope)
            if key not in seen:
                seen.add(key)
                normalized.append(entry)
        return sorted(normalized, key=lambda item: (item["scope"], item["name"].casefold()))

    def save_startup_snapshot(self, device_id: str, rows: list, *, now: int | None = None) -> dict:
        device_id = str(device_id)
        if not device_id or len(device_id) > 128:
            raise ValueError("invalid startup inventory device id")
        entries = self._normalize_startup_inventory(rows)
        captured_at = int(now if now is not None else time.time())
        if captured_at < 0:
            raise ValueError("invalid startup inventory timestamp")
        record = {"snapshot_id": uuid.uuid4().hex, "device_id": device_id,
                  "captured_at": captured_at, "entries": entries}
        with self._catalog_lock:
            history = self._catalog["startup_snapshots"].setdefault(device_id, [])
            if not isinstance(history, list):
                history = []
            history.append(record)
            self._catalog["startup_snapshots"][device_id] = history[-20:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_startup_snapshots(self, device_id: str, *, limit: int = 10) -> list[dict]:
        if not 1 <= int(limit) <= 20:
            raise ValueError("startup snapshot limit must be between 1 and 20")
        with self._catalog_lock:
            rows = self._catalog["startup_snapshots"].get(str(device_id), [])
            valid = []
            if isinstance(rows, list):
                for row in rows:
                    if (isinstance(row, dict) and isinstance(row.get("snapshot_id"), str)
                            and re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"])
                            and row.get("device_id") == str(device_id)
                            and type(row.get("captured_at")) is int and row["captured_at"] >= 0):
                        try:
                            if self._normalize_startup_inventory(row.get("entries")) == row.get("entries"):
                                valid.append(json.loads(json.dumps(row)))
                        except ValueError:
                            continue
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    def compare_startup_snapshots(self, device_id: str) -> dict:
        snapshots = self.list_startup_snapshots(device_id, limit=2)
        if len(snapshots) < 2:
            raise ValueError("at least two startup snapshots are required")
        earlier, later = snapshots[1], snapshots[0]
        before = {(item["name"], item["scope"]) for item in earlier["entries"]}
        after = {(item["name"], item["scope"]) for item in later["entries"]}
        changes = []
        for name, scope in sorted(before ^ after, key=lambda item: (item[1], item[0].casefold())):
            changes.append({"kind": "added" if (name, scope) in after else "removed", "name": name, "scope": scope})
        return {"before": earlier, "after": later, "changes": changes[:300]}

    @staticmethod
    def _normalize_service_inventory(rows: list) -> list[dict]:
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("service inventory must be a bounded list")
        states = {"Running", "Stopped", "Paused", "Start Pending", "Stop Pending", "Continue Pending", "Pause Pending"}
        start_types = {"Boot", "System", "Auto", "Manual", "Disabled"}
        normalized, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("service inventory rows must be objects")
            name, state, start_type = row.get("name"), row.get("state"), row.get("start_type")
            if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 160
                    or any(ord(char) < 32 for char in name) or state not in states or start_type not in start_types):
                raise ValueError("invalid service inventory entry")
            entry = {"name": name.strip(), "state": state, "start_type": start_type}
            key = entry["name"].casefold()
            if key not in seen:
                seen.add(key)
                normalized.append(entry)
        return sorted(normalized, key=lambda item: item["name"].casefold())

    def save_service_snapshot(self, device_id: str, rows: list, *, now: int | None = None) -> dict:
        device_id = str(device_id)
        if not device_id or len(device_id) > 128:
            raise ValueError("invalid service inventory device id")
        services = self._normalize_service_inventory(rows)
        captured_at = int(now if now is not None else time.time())
        if captured_at < 0:
            raise ValueError("invalid service inventory timestamp")
        record = {"snapshot_id": uuid.uuid4().hex, "device_id": device_id,
                  "captured_at": captured_at, "services": services}
        with self._catalog_lock:
            history = self._catalog["service_snapshots"].setdefault(device_id, [])
            if not isinstance(history, list):
                history = []
            history.append(record)
            self._catalog["service_snapshots"][device_id] = history[-20:]
            self._save_catalog()
        return json.loads(json.dumps(record))

    def list_service_snapshots(self, device_id: str, *, limit: int = 10) -> list[dict]:
        if not 1 <= int(limit) <= 20:
            raise ValueError("service snapshot limit must be between 1 and 20")
        with self._catalog_lock:
            rows = self._catalog["service_snapshots"].get(str(device_id), [])
            valid = []
            if isinstance(rows, list):
                for row in rows:
                    if (isinstance(row, dict) and isinstance(row.get("snapshot_id"), str)
                            and re.fullmatch(r"[0-9a-f]{32}", row["snapshot_id"])
                            and row.get("device_id") == str(device_id)
                            and type(row.get("captured_at")) is int and row["captured_at"] >= 0):
                        try:
                            if self._normalize_service_inventory(row.get("services")) == row.get("services"):
                                valid.append(json.loads(json.dumps(row)))
                        except ValueError:
                            continue
        valid.sort(key=lambda row: row["captured_at"], reverse=True)
        return valid[:int(limit)]

    def compare_service_snapshots(self, device_id: str) -> dict:
        snapshots = self.list_service_snapshots(device_id, limit=2)
        if len(snapshots) < 2:
            raise ValueError("at least two service snapshots are required")
        earlier, later = snapshots[1], snapshots[0]
        before = {item["name"]: item for item in earlier["services"]}
        after = {item["name"]: item for item in later["services"]}
        changes = []
        for name in sorted(set(before) | set(after), key=str.casefold):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            changes.append({"kind": "added" if old is None else "removed" if new is None else "changed",
                            "name": name, "before": {"state": old["state"], "start_type": old["start_type"]} if old else None,
                            "after": {"state": new["state"], "start_type": new["start_type"]} if new else None})
        return {"before": earlier, "after": later, "changes": changes[:500]}

    def record_network_traffic_sample(
        self, device_id: str, interface_id: str, label: str, *,
        sent_bytes: int, received_bytes: int, sampled_at: int,
    ) -> dict:
        """Keep one bounded, redacted counter baseline per device/interface."""
        device_id = str(device_id)
        if not device_id or len(device_id) > 128:
            raise ValueError("invalid network traffic device id")
        if not isinstance(interface_id, str) or re.fullmatch(r"[0-9a-f]{20}", interface_id) is None:
            raise ValueError("invalid network interface reference")
        if not isinstance(label, str):
            raise ValueError("invalid network interface label")
        label = " ".join("".join(ch if ch.isprintable() else " " for ch in label).split())[:64]
        if not label:
            raise ValueError("invalid network interface label")
        for value in (sent_bytes, received_bytes, sampled_at):
            if type(value) is not int or value < 0 or value > (1 << 64) - 1:
                raise ValueError("invalid network traffic counter or timestamp")

        current = {"interface_id": interface_id, "label": label, "sent_bytes": sent_bytes,
                   "received_bytes": received_bytes, "sampled_at": sampled_at}
        with self._catalog_lock:
            per_device = self._catalog["network_traffic_samples"].setdefault(device_id, {})
            if not isinstance(per_device, dict):
                per_device = {}
            previous = per_device.get(interface_id)
            if (isinstance(previous, dict) and previous.get("interface_id") == interface_id
                    and type(previous.get("sent_bytes")) is int and previous["sent_bytes"] >= 0
                    and type(previous.get("received_bytes")) is int and previous["received_bytes"] >= 0
                    and type(previous.get("sampled_at")) is int and previous["sampled_at"] >= 0):
                elapsed = sampled_at - previous["sampled_at"]
                if elapsed <= 0:
                    return {"state": "unknown", "interface_id": interface_id, "label": label,
                            "sent_delta": None, "received_delta": None, "elapsed_seconds": None,
                            "sampled_at": sampled_at}
                if elapsed > 24 * 60 * 60:
                    state, sent_delta, received_delta, elapsed_value = "baseline", None, None, None
                elif sent_bytes < previous["sent_bytes"] or received_bytes < previous["received_bytes"]:
                    state, sent_delta, received_delta, elapsed_value = "counter_reset", None, None, None
                else:
                    state = "delta"
                    sent_delta = sent_bytes - previous["sent_bytes"]
                    received_delta = received_bytes - previous["received_bytes"]
                    elapsed_value = elapsed
            else:
                state, sent_delta, received_delta, elapsed_value = "baseline", None, None, None

            # Keep only the newest bounded sample; stale callbacks never replace it.
            if not isinstance(previous, dict) or sampled_at > previous.get("sampled_at", -1):
                per_device[interface_id] = current
                if len(per_device) > 32:
                    oldest = min(per_device, key=lambda key: per_device[key].get("sampled_at", 0)
                                 if isinstance(per_device[key], dict) else 0)
                    per_device.pop(oldest, None)
                self._catalog["network_traffic_samples"][device_id] = per_device
                self._save_catalog()

        return {"state": state, "interface_id": interface_id, "label": label,
                "sent_delta": sent_delta, "received_delta": received_delta,
                "elapsed_seconds": elapsed_value, "sampled_at": sampled_at}

    def get_network_traffic_sample(self, device_id: str, interface_id: str) -> dict | None:
        """Return only the latest validated, redacted adapter counter sample."""
        key = str(device_id)
        if not isinstance(interface_id, str) or re.fullmatch(r"[0-9a-f]{20}", interface_id) is None:
            return None
        with self._catalog_lock:
            samples = self._catalog["network_traffic_samples"].get(key)
            row = samples.get(interface_id) if isinstance(samples, dict) else None
            if (not isinstance(row, dict) or row.get("interface_id") != interface_id
                    or not isinstance(row.get("label"), str)
                    or type(row.get("sampled_at")) is not int or row["sampled_at"] < 0):
                return None
            return {field: row[field] for field in ("interface_id", "label", "sampled_at")}

    @staticmethod
    def _default_network_traffic_alert(device_id: str) -> dict:
        return {"device_id": device_id, "enabled": False, "schedule_id": None,
                "interface_id": None, "label": None, "interval_seconds": 0,
                "threshold_bytes": None, "sent_bytes": None, "received_bytes": None,
                "bytes_used": 0, "sampled_at": None, "latched": False}

    @classmethod
    def _valid_network_traffic_alert(cls, row, *, expected_device_id: str | None = None) -> bool:
        fields = {"device_id", "enabled", "schedule_id", "interface_id", "label", "interval_seconds",
                  "threshold_bytes", "sent_bytes", "received_bytes", "bytes_used", "sampled_at", "latched"}
        if (not isinstance(row, dict) or set(row) != fields
                or not isinstance(row.get("device_id"), str) or not 1 <= len(row["device_id"]) <= 128
                or expected_device_id is not None and row["device_id"] != expected_device_id
                or type(row.get("enabled")) is not bool or type(row.get("latched")) is not bool
                or not isinstance(row.get("schedule_id"), str)
                or re.fullmatch(r"[0-9a-f]{32}", row["schedule_id"]) is None
                or not isinstance(row.get("interface_id"), str)
                or re.fullmatch(r"[0-9a-f]{20}", row["interface_id"]) is None
                or not isinstance(row.get("label"), str) or not 1 <= len(row["label"]) <= 64
                or any(ord(char) < 32 for char in row["label"])
                or type(row.get("interval_seconds")) is not int
                or row["interval_seconds"] not in {0, 900, 1800, 3600}
                or type(row.get("threshold_bytes")) is not int
                or row["threshold_bytes"] not in {100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000}
                or type(row.get("bytes_used")) is not int or row["bytes_used"] < 0):
            return False
        for key in ("sent_bytes", "received_bytes", "sampled_at"):
            value = row.get(key)
            if value is not None and (type(value) is not int or value < 0 or value > (1 << 64) - 1):
                return False
        if row["enabled"]:
            return row["interval_seconds"] in {900, 1800, 3600}
        return row["interval_seconds"] == 0

    def get_network_traffic_alert(self, device_id: str) -> dict:
        key = str(device_id)
        with self._catalog_lock:
            row = self._catalog["network_traffic_alerts"].get(key)
            if not self._valid_network_traffic_alert(row, expected_device_id=key):
                return self._default_network_traffic_alert(key)
            return json.loads(json.dumps(row))

    def configure_network_traffic_alert(
        self, device_id: str, *, schedule_id: str, interface_id: str, label: str,
        interval_seconds: int, threshold_bytes: int, enabled: bool,
    ) -> dict:
        device_id = str(device_id)
        if not 1 <= len(device_id) <= 128:
            raise ValueError("invalid network traffic alert device id")
        if type(enabled) is not bool or type(interval_seconds) is not int:
            raise ValueError("invalid network traffic alert state")
        if interval_seconds not in ({900, 1800, 3600} if enabled else {0}):
            raise ValueError("unsupported network traffic alert interval")
        if not isinstance(schedule_id, str) or re.fullmatch(r"[0-9a-f]{32}", schedule_id) is None:
            raise ValueError("invalid network traffic alert schedule id")
        if not isinstance(interface_id, str) or re.fullmatch(r"[0-9a-f]{20}", interface_id) is None:
            raise ValueError("invalid network interface reference")
        if (not isinstance(label, str) or not 1 <= len(label) <= 64
                or any(ord(char) < 32 for char in label)):
            raise ValueError("invalid network interface label")
        if type(threshold_bytes) is not int or threshold_bytes not in {100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000}:
            raise ValueError("unsupported network traffic threshold")
        row = {**self._default_network_traffic_alert(device_id), "enabled": enabled,
               "schedule_id": schedule_id, "interface_id": interface_id, "label": label,
               "interval_seconds": interval_seconds, "threshold_bytes": threshold_bytes}
        with self._catalog_lock:
            self._catalog["network_traffic_alerts"][device_id] = row
            self._save_catalog()
        return json.loads(json.dumps(row))

    def record_network_traffic_alert_sample(
        self, device_id: str, *, schedule_id: str, interface_id: str,
        sent_bytes: int, received_bytes: int, sampled_at: int, now: int | None = None,
    ) -> dict:
        key = str(device_id)
        current_time = int(time.time() if now is None else now)
        if type(current_time) is not int or current_time < 0:
            return {"state": "unknown", "alert": None, "bytes_used": 0}
        for value in (sent_bytes, received_bytes, sampled_at):
            if type(value) is not int or value < 0 or value > (1 << 64) - 1:
                return {"state": "unknown", "alert": None, "bytes_used": 0}
        with self._catalog_lock:
            old = self._catalog["network_traffic_alerts"].get(key)
            if not self._valid_network_traffic_alert(old, expected_device_id=key):
                return {"state": "unknown", "alert": None, "bytes_used": 0}
            row = json.loads(json.dumps(old))
            if (not row["enabled"] or row["schedule_id"] != schedule_id
                    or row["interface_id"] != interface_id):
                return {"state": "unknown", "alert": None, "bytes_used": row["bytes_used"]}
            age = current_time - sampled_at
            if age < -60 or age > row["interval_seconds"] * 2 + 300:
                return {"state": "unknown", "alert": None, "bytes_used": row["bytes_used"]}
            if type(row["sampled_at"]) is int and sampled_at <= row["sampled_at"]:
                return {"state": "unknown", "alert": None, "bytes_used": row["bytes_used"]}
            state = "baseline"
            alert = None
            if type(row["sent_bytes"]) is int and type(row["received_bytes"]) is int:
                if sent_bytes < row["sent_bytes"] or received_bytes < row["received_bytes"]:
                    state = "counter_reset"
                else:
                    row["bytes_used"] += (sent_bytes - row["sent_bytes"]) + (received_bytes - row["received_bytes"])
                    state = "tracked"
                    if row["bytes_used"] >= row["threshold_bytes"] and not row["latched"]:
                        row["latched"] = True
                        state = "alert"
                        alert = {"threshold_bytes": row["threshold_bytes"],
                                 "observed_bytes": row["bytes_used"], "label": row["label"],
                                 "sampled_at": sampled_at}
                    elif row["latched"]:
                        state = "latched"
            row["sent_bytes"], row["received_bytes"], row["sampled_at"] = sent_bytes, received_bytes, sampled_at
            self._catalog["network_traffic_alerts"][key] = row
            self._save_catalog()
        return {"state": state, "alert": alert, "bytes_used": row["bytes_used"]}

    def save_navigation_state(
        self, user_id: int, selected_device_id: str | None, page_key: str,
        page_cursor: int, stack: list[dict], *, now: int | None = None,
        ttl_seconds: int = 86_400,
    ) -> dict:
        """Persist a bounded, user-scoped back stack without pending form data."""
        if int(user_id) <= 0:
            raise ValueError("user_id must be positive")
        if selected_device_id is not None and self.get(str(selected_device_id)) is None:
            raise ValueError("selected device does not exist")
        key = str(page_key).strip()
        if not key or len(key) > 128:
            raise ValueError("page_key must contain 1-128 characters")
        if not isinstance(page_cursor, int) or page_cursor < 0:
            raise ValueError("page_cursor must be a non-negative integer")
        if not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 86_400:
            raise ValueError("navigation TTL must be between one minute and one day")
        if not isinstance(stack, list) or len(stack) > 50 or any(not isinstance(item, dict) for item in stack):
            raise ValueError("back stack must contain at most 50 page records")
        safe_stack = []
        for item in stack:
            page = str(item.get("page") or "")
            if not page or len(page) > 64:
                raise ValueError("back-stack page names must contain 1-64 characters")
            record = {"page": page}
            for field in ("area", "feature_id", "device_id"):
                value = item.get(field)
                if value is not None:
                    record[field] = str(value)[:128]
            cursor = item.get("cursor")
            if cursor is not None:
                if not isinstance(cursor, int) or cursor < 0:
                    raise ValueError("back-stack cursor must be non-negative")
                record["cursor"] = cursor
            safe_stack.append(record)
        current = int(now if now is not None else time.time())
        state = {
            "user_id": int(user_id),
            "selected_device_id": str(selected_device_id) if selected_device_id else None,
            "page_key": key,
            "page_cursor": page_cursor,
            "stack": safe_stack,
            "updated_at": current,
            "expires_at": current + ttl_seconds,
        }
        with self._catalog_lock:
            self._catalog["navigation"][str(int(user_id))] = state
            self._save_catalog()
        return json.loads(json.dumps(state))

    def get_navigation_state(self, user_id: int, *, now: int | None = None) -> dict | None:
        current = int(now if now is not None else time.time())
        with self._catalog_lock:
            key = str(int(user_id))
            state = self._catalog["navigation"].get(key)
            if not isinstance(state, dict):
                return None
            if current >= int(state.get("expires_at", 0)):
                del self._catalog["navigation"][key]
                self._save_catalog()
                return None
            result = json.loads(json.dumps(state))
        selected = result.get("selected_device_id")
        if selected is not None and self.get(selected) is None:
            result["selected_device_id"] = None
            result["stale_device"] = True
        else:
            result["stale_device"] = False
        return result

    @staticmethod
    def _normalize_feature_synonym(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("search synonym must be text")
        normalized = " ".join(value.split())
        if not 1 <= len(normalized) <= 64 or any(
            unicodedata.category(char) == "Cc" for char in normalized
        ):
            raise ValueError("search synonym must contain 1-64 printable characters")
        return normalized

    def get_feature_search_synonyms(self, *, owner_id: int) -> dict[str, list[str]]:
        """Return a sanitized copy of the owner's shared catalog vocabulary."""
        if int(owner_id) <= 0:
            return {}
        with self._catalog_lock:
            stored = self._catalog["feature_search_synonyms"].get(str(int(owner_id)), {})
            if not isinstance(stored, dict):
                return {}
            result: dict[str, list[str]] = {}
            used: set[str] = set()
            for feature_id, aliases in stored.items():
                if not isinstance(feature_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{1,3}-F\d{2}", feature_id):
                    continue
                if not isinstance(aliases, list):
                    continue
                clean: list[str] = []
                for alias in aliases[:16]:
                    try:
                        value = self._normalize_feature_synonym(alias)
                    except ValueError:
                        continue
                    folded = value.casefold()
                    if folded in used:
                        continue
                    used.add(folded)
                    clean.append(value)
                if clean:
                    result[feature_id] = clean
            return result

    def add_feature_search_synonym(
        self, feature_id: str, synonym: str, *, actor_id: int, owner_id: int,
    ) -> list[str]:
        """Add one unambiguous alias to the owner-managed, shared search vocabulary."""
        if int(owner_id) <= 0 or int(actor_id) != int(owner_id):
            raise PermissionError("only the configured owner may edit catalog synonyms")
        feature = str(feature_id or "")
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,3}-F\d{2}", feature):
            raise ValueError("invalid feature ID")
        alias = self._normalize_feature_synonym(synonym)
        folded = alias.casefold()
        owner_key = str(int(owner_id))
        with self._catalog_lock:
            owner_rows = self._catalog["feature_search_synonyms"].setdefault(owner_key, {})
            if not isinstance(owner_rows, dict):
                owner_rows = {}
                self._catalog["feature_search_synonyms"][owner_key] = owner_rows
            current = owner_rows.get(feature, [])
            if not isinstance(current, list):
                current = []
            for other_feature, aliases in owner_rows.items():
                if not isinstance(aliases, list):
                    continue
                for existing in aliases:
                    try:
                        normalized = self._normalize_feature_synonym(existing)
                    except ValueError:
                        continue
                    if normalized.casefold() == folded:
                        if other_feature == feature:
                            return self.get_feature_search_synonyms(owner_id=owner_id).get(feature, [])
                        raise ValueError("search synonym is already assigned to another feature")
            if len(current) >= 16:
                raise ValueError("feature already has the maximum of 16 search synonyms")
            owner_rows[feature] = [*current, alias]
            self._save_catalog()
            return list(owner_rows[feature])

    def remove_feature_search_synonym(
        self, feature_id: str, synonym: str, *, actor_id: int, owner_id: int,
    ) -> bool:
        if int(owner_id) <= 0 or int(actor_id) != int(owner_id):
            raise PermissionError("only the configured owner may edit catalog synonyms")
        feature = str(feature_id or "")
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,3}-F\d{2}", feature):
            raise ValueError("invalid feature ID")
        alias = self._normalize_feature_synonym(synonym).casefold()
        owner_key = str(int(owner_id))
        with self._catalog_lock:
            owner_rows = self._catalog["feature_search_synonyms"].get(owner_key)
            aliases = owner_rows.get(feature) if isinstance(owner_rows, dict) else None
            if not isinstance(aliases, list):
                return False
            kept = []
            removed = False
            for item in aliases:
                try:
                    matches = self._normalize_feature_synonym(item).casefold() == alias
                except ValueError:
                    matches = False
                if matches and not removed:
                    removed = True
                else:
                    kept.append(item)
            if not removed:
                return False
            if kept:
                owner_rows[feature] = kept
            else:
                del owner_rows[feature]
            if not owner_rows:
                del self._catalog["feature_search_synonyms"][owner_key]
            self._save_catalog()
            return True
