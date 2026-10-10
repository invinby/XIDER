"""Pure companion presentation; catalogue availability is not installation.

Records are component-specific local inputs, not a new agent wire protocol.
The current Guardian response supplies its own version and receive timestamp;
it does not supply absence inventory or an owner-removal acknowledgement.
"""

from __future__ import annotations

import math

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


def component_state(name: str, os_name: str, *, record: dict | None = None,
                    checked_at: float | None = None, releases=(), now: float) -> dict:
    if name not in NAMES:
        raise ValueError("Unknown companion")
    component = component_for(os_name, keeper=True)
    result = {"name": NAMES[name], "component": component if name == "keeper" else None,
              "state": "unknown", "reason": None, "version": None,
              "available_version": None, "checked_at": checked_at}
    if component is None:
        return {**result, "state": "unsupported", "reason": "unsupported_os"}
    if name != "keeper":
        return {**result, "reason": "not_implemented", "checked_at": None}
    packages = [release for release in releases if release.signed_package(component)]
    if packages:
        result["available_version"] = max(packages, key=lambda release: release.version).tag
    record = record if isinstance(record, dict) else {}
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
    if record.get("installed") is not True and record.get("version_source") != "guardian":
        return {**result, "reason": "missing_inventory"}
    version = record.get("version")
    if not isinstance(version, str) or not version.strip() or version.strip().lower() in {"?", "unknown", "none"}:
        return {**result, "reason": "missing_version" if record.get("installed") is True else "missing_inventory"}
    return {**result, "state": "installed", "version": version.strip()[:128]}


def device_companions(info: dict, releases=(), *, now: float) -> list[dict]:
    """Bridge existing stored Guardian telemetry only; no agent-version fallback."""
    os_name = str(info.get("os") or "")
    guardian = info.get("guardian")
    if isinstance(guardian, dict) and guardian.get("version_source") != "guardian":
        # Legacy bot caches may have filled this slot with the worker version.
        # A new explicit Guardian response proves provenance without a migration.
        guardian = {key: value for key, value in guardian.items() if key != "version"}
    return [component_state(name, os_name,
                            record=guardian if name == "keeper" else None,
                            checked_at=info.get("guardian_last_seen") if name == "keeper" else None,
                            releases=releases, now=now) for name in NAMES]
