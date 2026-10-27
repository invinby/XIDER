"""Pure companion presentation; catalogue availability is not installation.

Records are component-specific local inputs. The macOS Guardian may relay fresh
local Safari/Siri health markers, but it does not claim absence inventory or an
owner-removal acknowledgement for those layers.
"""

from __future__ import annotations

import math
import re

from release_catalog import component_for


FRESHNESS_SECONDS = 150
NAMES = {"keeper": "Guard Keeper", "safari": "Safari", "siri": "Siri"}
REASONS = {
    "missing_inventory": "нет отдельного отчёта компонента",
    "missing_version": "нет собственной версии компонента",
    "stale": "отдельный отчёт устарел; состояние неизвестно",
    "offline": "компонент оффлайн; состояние неизвестно",
    "owner_removed": "владелец явно удалил компонент; установка не подтверждена",
    "not_implemented": "XIDER-компонент не реализован; нет телеметрии и подписанного пакета",
    "unsupported_os": "ОС не поддерживается",
}


def clean_layer_records(value: object, *, now: float) -> dict:
    """Accept only fresh, component-owned Safari/Siri heartbeat records."""
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for name in ("safari", "siri"):
        record = value.get(name)
        if not isinstance(record, dict):
            continue
        version = record.get("version")
        stamp = record.get("last_seen")
        pid = record.get("pid")
        state = record.get("state")
        if (record.get("installed") is not True or record.get("version_source") != name
                or not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or stamp <= 0 or stamp > now + 5
                or type(pid) is not int or pid <= 0
                or not isinstance(state, str)
                or state not in {"running", "waiting", "error", "stopped"}):
            continue
        cleaned[name] = {
            "installed": True, "version": version, "version_source": name,
            "state": state, "pid": pid, "last_seen": stamp,
        }
    return cleaned


def component_state(name: str, os_name: str, *, record: dict | None = None,
                    checked_at: float | None = None, releases=(), now: float) -> dict:
    if name not in NAMES:
        raise ValueError("Unknown companion")
    component = component_for(os_name, keeper=True) if name == "keeper" else None
    result = {"name": NAMES[name], "component": component if name == "keeper" else None,
              "state": "unknown", "reason": None, "version": None,
              "available_version": None, "checked_at": checked_at}
    if name == "keeper" and component is None:
        return {**result, "state": "unsupported", "reason": "unsupported_os"}
    if name in {"safari", "siri"} and "mac" not in str(os_name or "").lower() and "darwin" not in str(os_name or "").lower():
        return {**result, "state": "unsupported", "reason": "unsupported_os", "checked_at": None}
    packages = [release for release in releases if component and release.signed_package(component)]
    if packages:
        result["available_version"] = max(packages, key=lambda release: release.version).tag
    record = record if isinstance(record, dict) else {}
    if name in {"safari", "siri"} and not record:
        return {**result, "reason": "missing_inventory", "checked_at": None}
    # These overlays require an explicit component/local signal; never derive
    # them from a stopped worker, missing scheduled task, or missing fields.
    if record.get("owner_removed") is True:
        return {**result, "reason": "owner_removed"}
    if record.get("offline") is True:
        return {**result, "reason": "offline"}
    if not record:
        return {**result, "reason": "missing_inventory"}
    if (type(checked_at) not in (int, float) or not math.isfinite(checked_at)
            or checked_at <= 0 or not 0 <= now - checked_at < FRESHNESS_SECONDS):
        return {**result, "reason": "stale"}
    if record.get("installed") is False:
        return {**result, "state": "available" if result["available_version"] else "not_installed"}
    if name in {"safari", "siri"} and record.get("version_source") != name:
        return {**result, "reason": "missing_inventory", "checked_at": None}
    if name == "keeper" and record.get("installed") is not True and record.get("version_source") != "guardian":
        return {**result, "reason": "missing_inventory"}
    version = record.get("version")
    if not isinstance(version, str) or not version.strip() or version.strip().lower() in {"?", "unknown", "none"}:
        return {**result, "reason": "missing_version" if record.get("installed") is True else "missing_inventory"}
    state = record.get("state") if name in {"safari", "siri"} else "installed"
    if state not in {"running", "waiting", "error", "stopped", "installed"}:
        state = "installed"
    return {**result, "state": state, "version": version.strip()[:128]}


def device_companions(info: dict, releases=(), *, now: float) -> list[dict]:
    """Use component-owned fresh inventory; never infer a companion from Agent."""
    os_name = str(info.get("os") or "")
    guardian = info.get("guardian")
    if isinstance(guardian, dict) and guardian.get("version_source") != "guardian":
        # Legacy bot caches may have filled this slot with the worker version.
        # A new explicit Guardian response proves provenance without a migration.
        guardian = {key: value for key, value in guardian.items() if key != "version"}
    capabilities = info.get("capabilities")
    companion_records = info.get("companion_records")
    if not isinstance(companion_records, dict):
        companion_records = capabilities.get("companions") if isinstance(capabilities, dict) else {}
    if not isinstance(companion_records, dict):
        companion_records = {}
    return [component_state(
        name, os_name,
        record=guardian if name == "keeper" else companion_records.get(name),
        checked_at=(info.get("guardian_last_seen") if name == "keeper"
                    else (companion_records.get(name) or {}).get("last_seen")),
        releases=releases, now=now,
    ) for name in NAMES]
