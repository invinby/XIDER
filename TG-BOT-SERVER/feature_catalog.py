"""Read-only catalog discovery; registry metadata is never a dispatch table."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Collection, Mapping


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs/features/feature-registry.json"
_SPACE = re.compile(r"\s+")
RESULT_TYPES = frozenset({"text", "image", "file", "status"})
OS_PERMISSION_CAPABILITIES = {
    "screen_recording": "screen_recording",
    "camera": "camera",
    "microphone": "microphone",
    "one_shot_location_consent": "location",
}
NON_OS_PERMISSION_REQUIREMENTS = frozenset({
    "owner_role_or_explicit_grant", "owner_file_access",
    "one_time_owner_shell_approval", "owner_guardian_or_power_confirmation",
    "explicit_local_effect_consent", "bounded_diagnostic_data_access",
    "exact_release_and_target_owner_approval", "owner_server_action_confirmation",
    "permission_status_check", "display_control",
})
_BUTTON_TEXT = {
    "areas": "🧭 Каталог функций",
    "previous": "◀ Назад",
    "next": "Дальше ▶",
    "sections": "К разделам",
    "detail_back": "К разделу",
    "search_exact": "🔎 По ID и названию",
    "search_outcome": "🎯 По желаемому результату",
    "search_fuzzy": "🧩 Поиск с опечатками",
    "filter_result": "🎛 По типу результата",
    "filter_permission": "🔐 По разрешениям ОС",
    "filter_access": "✅ Доступно мне",
    "result_text": "📝 Текст",
    "result_image": "🖼 Изображение",
    "result_file": "📎 Файл",
    "result_status": "🔘 Статус",
    "synonym_add": "🗣 Мои слова для поиска",
    "synonym_cancel": "Отмена",
    "location_compare": "↔️ Сравнить источники",
    "traffic_threshold": "🔔 Порог трафика",
    "audit_filter": "🔎 Фильтр журнала",
    "audit_export": "📤 Экспорт журнала",
    "audit_event_row": "Запись журнала",
    "admin_stale_grants": "🔎 Права на отсутствующие устройства",
    "stale_grants_refresh": "↻ Перепроверить",
    "access_review": "🧾 Обзор ролей и прав",
    "access_review_refresh": "↻ Снять новый срез",
    "access_review_previous": "◀ Предыдущая страница",
    "access_review_next": "Следующая страница ▶",
    "access_requests": "📥 Заявки на доступ",
    "access_requests_refresh": "↻ Обновить очередь",
    "access_request_approve": "✅ Одобрить на 24 часа",
    "access_request_reject": "✖ Отклонить",
    "access_requests_previous": "◀ Предыдущая страница",
    "access_requests_next": "Следующая страница ▶",
    "bulk_grant": "👥 Массовая выдача прав",
    "bulk_revoke": "🧹 Массовый отзыв прав",
    "permission_profile_save": "➕ Сохранить права пользователя как профиль",
    "permission_profile_row": "📋",
    "cpu_history_15s": "15 секунд · 5 с шагом",
    "cpu_history_30s": "30 секунд · 5 с шагом",
    "cpu_history_60s": "60 секунд · 5 с шагом",
    "memory_history_15s": "15 секунд · 5 с шагом",
    "memory_history_30s": "30 секунд · 5 с шагом",
    "memory_history_60s": "60 секунд · 5 с шагом",
    "memory_pressure_history_15s": "15 секунд · 5 с шагом",
    "memory_pressure_history_30s": "30 секунд · 5 с шагом",
    "memory_pressure_history_60s": "60 секунд · 5 с шагом",
    "microphone_preview": "🎧 Записать, прослушать и отправить",
    "microphone_preview_send": "📤 Отправить аудио в чат",
    "microphone_preview_discard": "🗑 Отменить и удалить",
    "shell_stop": "⏹ Остановить выполнение",
    "shell_dry_run": "🧪 Подготовить dry-run",
    "shell_runbook_confirm": "✅ Подтвердить план",
    "shell_template_save": "💾 Сохранить шаблон",
    "shell_runbook_new": "➕ Новый план",
    "shell_result_preview": "👁 Показать очищенный вывод",
}


def button_text(key: str) -> str:
    try:
        return _BUTTON_TEXT[key]
    except KeyError as exc:
        raise ValueError("unknown feature catalog label") from exc


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """Load static metadata only; runtime handlers are registered elsewhere."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("feature registry is unavailable or malformed") from exc
    if not isinstance(value, dict) or not isinstance(value.get("features"), list):
        raise ValueError("feature registry has an invalid shape")
    return value


def _normalize(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip().casefold()


def search_features(
    registry: Mapping[str, Any], query: str, *, mode: str,
    synonyms: Mapping[str, Collection[str]] | None = None,
) -> list[dict[str, Any]]:
    """Search identity/title separately from user-outcome wording."""
    if mode not in {"exact", "outcome", "fuzzy"}:
        raise ValueError("search mode must be exact, outcome or fuzzy")
    needle = _normalize(query)
    if not needle:
        return []
    rows = registry.get("features", [])
    if not isinstance(rows, list):
        raise ValueError("feature registry has an invalid shape")
    matched = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        if mode == "fuzzy":
            # Fuzzy matching is intentionally limited to identity and title;
            # outcome text stays in its explicit, separate search mode.
            identity = " ".join(str(value or "") for value in (item.get("feature_id"), item.get("title")))
            tokens = re.findall(r"[^\W_]+", _normalize(identity), flags=re.UNICODE)
            query_tokens = re.findall(r"[^\W_]+", needle, flags=re.UNICODE)
            if not query_tokens or len(needle) > 120:
                continue
            available = list(tokens)
            scores = []
            for query_token in query_tokens:
                if query_token in available:
                    available.remove(query_token)
                    scores.append(1.0)
                    continue
                if len(query_token) < 4:
                    scores = []
                    break
                candidates = [
                    (SequenceMatcher(None, query_token, token, autojunk=False).ratio(), index)
                    for index, token in enumerate(available)
                    if len(token) >= 4
                ]
                if not candidates:
                    scores = []
                    break
                score, index = max(candidates)
                if score < 0.78:
                    scores = []
                    break
                available.pop(index)
                scores.append(score)
            if scores and len(scores) == len(query_tokens):
                matched.append((sum(scores) / len(scores), str(item.get("feature_id") or ""), dict(item)))
            continue
        if mode == "exact":
            aliases = synonyms.get(str(item.get("feature_id") or ""), []) if isinstance(synonyms, Mapping) else []
            if not isinstance(aliases, (list, tuple, set, frozenset)):
                aliases = []
            haystacks = (
                item.get("feature_id"), item.get("label_id"), item.get("title"),
                item.get("description"), *aliases,
            )
        else:
            haystacks = (item.get("outcome"),)
        if any(needle in _normalize(value) for value in haystacks):
            matched.append(dict(item))
    if mode == "fuzzy":
        return [item for _score, _feature_id, item in sorted(matched, key=lambda row: (-row[0], row[1]))]
    return matched


def filter_features_by_result_type(
    registry: Mapping[str, Any], result_type: str,
) -> list[dict[str, Any]]:
    """Return only rows with an explicit, valid result-type declaration."""
    if not isinstance(result_type, str) or result_type not in RESULT_TYPES:
        raise ValueError("unknown result type")
    rows = registry.get("features", [])
    if not isinstance(rows, list):
        raise ValueError("feature registry has an invalid shape")
    matched = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        declared = item.get("result_types")
        if (
            isinstance(declared, list)
            and declared
            and all(isinstance(value, str) and value in RESULT_TYPES for value in declared)
            and result_type in declared
        ):
            matched.append(dict(item))
    return matched


def filter_features_by_permission_state(
    registry: Mapping[str, Any], permission_states: Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    """Keep rows with no OS privacy requirement or a reported granted status.

    The registry's permissions field also includes RBAC/confirmation gates.
    Those are deliberately not treated as operating-system grants; normal
    visibility and callback authorization checks remain authoritative.
    """
    rows = registry.get("features", [])
    if not isinstance(rows, list):
        raise ValueError("feature registry has an invalid shape")
    states = permission_states if isinstance(permission_states, Mapping) else {}
    matched = []
    known_requirements = set(OS_PERMISSION_CAPABILITIES) | set(NON_OS_PERMISSION_REQUIREMENTS)
    for item in rows:
        if not isinstance(item, dict):
            continue
        requirements = item.get("permissions")
        if (
            not isinstance(requirements, list)
            or any(not isinstance(value, str) or value not in known_requirements for value in requirements)
        ):
            continue
        required_capabilities = [OS_PERMISSION_CAPABILITIES[value] for value in requirements
                                 if value in OS_PERMISSION_CAPABILITIES]
        if all(states.get(capability) == "granted" for capability in required_capabilities):
            matched.append(dict(item))
    return matched


def filter_features_for_access(
    registry: Mapping[str, Any], *, role: str, grants: Collection[str],
    platform: str,
) -> list[dict[str, Any]]:
    """Return non-planned catalog rows allowed by role/grant and platform.

    Device authorization is checked by the Telegram caller before selecting a
    platform. This function evaluates only explicit registry policy and never
    resolves or invokes a runtime handler.
    """
    if role == "blocked":
        return []
    if role not in {"owner", "user", "guest"}:
        raise ValueError("unknown role")
    if platform not in {"windows", "macos", "linux"}:
        return []
    rows = registry.get("features", [])
    if not isinstance(rows, list):
        raise ValueError("feature registry has an invalid shape")
    matched = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        allowed_roles = item.get("role")
        platforms = item.get("platforms")
        if (
            not isinstance(allowed_roles, list)
            or role not in allowed_roles
            or not isinstance(platforms, list)
            or platform not in platforms
            or item.get("support_state") not in {"implemented", "needs_device_test"}
            or not _grant_allows(item, role, grants)
        ):
            continue
        matched.append(dict(item))
    matched.sort(key=lambda item: (str(item.get("area", "")), str(item.get("feature_id", ""))))
    return matched


def _grant_allows(feature: Mapping[str, Any], role: str, grants: Collection[str]) -> bool:
    if role == "owner":
        return True
    grant = feature.get("grant")
    if not isinstance(grant, Mapping):
        return False
    kind = grant.get("kind")
    if kind == "owner_only":
        return False
    if kind in {"public", "none"}:
        return True
    key = grant.get("key")
    return kind == "delegable" and isinstance(key, str) and key in grants


def explain_availability(
    feature: Mapping[str, Any], *, role: str, grants: Collection[str],
    platform: str | None, permission_states: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """Explain policy gates from registry metadata and a supplied fresh OS snapshot.

    The function is read-only and never infers a permission from an absent or
    malformed capability state. Callers must only pass permission states from
    a validated, fresh device snapshot.
    """
    if role not in {"owner", "user", "guest", "blocked"}:
        raise ValueError("unknown role")
    blockers: list[dict[str, str]] = []
    allowed_roles = feature.get("role")
    if role == "blocked" or not isinstance(allowed_roles, list) or role not in allowed_roles:
        blockers.append({"kind": "role", "state": "blocked"})
    if not _grant_allows(feature, role, grants):
        blockers.append({"kind": "grant", "state": "missing"})
    platforms = feature.get("platforms")
    if platform is not None and (
        not isinstance(platforms, list) or platform not in platforms
    ):
        blockers.append({"kind": "os", "state": "unsupported"})

    states = permission_states if isinstance(permission_states, Mapping) else {}
    permissions = feature.get("permissions")
    if isinstance(permissions, list):
        for requirement in permissions:
            capability = OS_PERMISSION_CAPABILITIES.get(requirement) if isinstance(requirement, str) else None
            if capability is None:
                continue
            state = states.get(capability)
            if state not in {"granted", "denied", "restricted", "unsupported"}:
                state = "unknown"
            if state != "granted":
                blockers.append({"kind": "permission", "id": capability, "state": state})
    support_state = feature.get("support_state")
    if support_state not in {"implemented", "needs_device_test"}:
        blockers.append({"kind": "readiness", "state": str(support_state or "unknown")})
    return blockers


_STATIC_NAVIGATION_LABELS = {
    "terminal_shell_runner_diagnostic": "🧪 Проверить shell runner",
    "terminal_shell_late_results": "📥 Поздние результаты shell",
    "terminal_shell_approval_inbox": "📥 Ожидают подтверждения",
    "terminal_shell_timing": "⏱ Shell: время выполнения",
    "terminal_shell_runbook": "🧾 План команд · подтверждение каждого шага",
    "terminal_shell_runbook_templates": "📚 Шаблоны runbook",
    "terminal_shell_metadata_export": "📤 Экспорт shell-метаданных",
    "network_dns_resolution": "🌐 DNS-проверка",
    "network_dns_settings": "🧭 DNS-настройки",
    "network_dns_comparison": "🧪 Сравнить DNS",
    "network_gateway_probe": "🛜 Проверить шлюз",
    "network_captive_portal": "🛜 Проверить captive portal",
    "network_ipv6_connectivity": "🌐 Проверить IPv6",
    "network_tls_certificate": "🔐 Проверить TLS-сертификат",
    "network_service_probe": "🌐 Проверить сервис",
    "network_latency_series": "📈 Серия latency",
    "network_packet_loss_history": "📉 Потери пакетов",
    "network_jitter": "📶 Jitter сети",
    "network_throughput": "🚀 Пропускная способность",
    "network_link_speed": "📶 Скорость линка",
    "network_route_lookup": "🧭 Таблица маршрутов",
    "network_wifi_quality_history": "📈 Тренд Wi-Fi",
    "network_wifi_channel_summary": "📡 Канал Wi-Fi",
    "network_wifi_connection_history": "🧾 История Wi-Fi",
    "network_proxy_status": "🛡 Proxy-настройки",
    "network_vpn_route_status": "🧭 VPN-маршрут",
    "network_endpoint_batch": "🌐 Набор endpoints",
    "network_dnssec_check": "🔐 Проверить DNSSEC",
    "network_path_diagnostic": "🧭 Где обрыв?",
    "network_check_sets": "🌐 Сохранённые наборы",
    "network_external_ip_watch": "🌍 Оповещение о смене IP",
    "network_interface_traffic": "📊 Трафик адаптера",
    "network_route_trace": "🧭 Трассировка маршрута",
    "network_path_mtu": "🧭 Диагностика MTU-пути",
}


def navigation_button_label(page: Mapping[str, Any]) -> str | None:
    """Return local, non-X-LEX labels for newly registered static renderers."""
    return _STATIC_NAVIGATION_LABELS.get(str(page.get("presentation") or ""))


def visible_navigation_pages(
    registry: Mapping[str, Any], *, parent: str, role: str,
    grants: Collection[str], platform: str | None = None, state: str | None = None,
) -> list[dict[str, Any]]:
    """Filter declarative page metadata; never resolve or invoke its callback."""
    if role == "blocked":
        return []
    if role not in {"owner", "user", "guest"}:
        raise ValueError("unknown role")
    rows = registry.get("navigation")
    if not isinstance(rows, list):
        raise ValueError("feature registry navigation is malformed")
    visible = []
    required = {
        "page_id", "parent", "label_id", "callback", "roles", "grants",
        "platforms", "permissions", "support_state", "style", "presentation",
    }
    for row in rows:
        if not isinstance(row, Mapping) or not required.issubset(row):
            raise ValueError("feature registry navigation is malformed")
        if not isinstance(row["roles"], list) or not isinstance(row["grants"], list):
            raise ValueError("feature registry navigation is malformed")
        if not isinstance(row["platforms"], list) or not isinstance(row["permissions"], list):
            raise ValueError("feature registry navigation is malformed")
        condition = row.get("when")
        if condition not in {None, "awake", "standby"}:
            raise ValueError("feature registry navigation state is malformed")
        if row["parent"] != parent or role not in row["roles"]:
            continue
        if condition is not None and state != condition:
            continue
        if platform is not None and platform not in row["platforms"]:
            continue
        if row["support_state"] != "implemented":
            continue
        if row["presentation"] not in {
            "nav", "versions", "capabilities", "device_tags", "device_tags_action", "device_purpose", "device_purpose_choice", "install_os", "devices_row", "devices_list_action", "all_device_action", "snapshot_detail_action", "snapshot_delete_action", "file_inbox", "file_inbox_action", "file_inbox_row", "home_devices", "home_server",
                  "home_admin", "home_catalog", "home_about", "home_access_request", "permission_requests", "permission_request_row", "permission_request_decision", "screen_action", "camera_quality",
                  "catalog_area", "catalog_action", "catalog_feature", "mic_duration", "microphone_level", "microphone_balance", "microphone_history", "audio_output_catalog", "microphone_test", "microphone_preview",
                "audio_output_switch",
            "catalog_result_type",
            "catalog_synonym", "catalog_synonym_cancel",
            "volume_level", "rotate_angle", "url_count", "url_count_action",
            "power_confirmation",
            "url_favorite_open", "url_favorite_delete", "guest_device_row",
            "admin_user_row", "admin_users_empty",
                "admin_user_role", "admin_user_block", "admin_user_action", "admin_user_role_history", "admin_user_grant_history", "admin_user_revoke_all_grants", "admin_user_pause_grants", "admin_user_grant_undo", "admin_user_permission_profiles", "admin_permission_profile_save", "admin_permission_profile_row", "admin_permission_profile_page", "admin_permission_profile_cancel", "admin_owner_invariant", "admin_rbac_snapshot", "admin_rbac_compare", "audit_filter", "audit_event_row",
            "audit_export",
            "admin_device_grant", "admin_device_temporary_grant",
            "admin_permission_grant", "admin_permission_temporary_grant", "admin_stale_grants", "stale_grants_refresh",
            "access_review", "access_review_refresh", "access_review_page", "bulk_grant", "bulk_revoke",
            "admin_text_preview", "admin_style_choice",
            "snapshot_device_row", "snapshot_selection_action",
            "snapshot_catalog_row", "snapshot_catalog_action", "device_collection_row", "saved_filter_row", "device_sort_choice", "device_pin_toggle", "device_order_move", "device_archive", "device_archive_row", "device_archive_page", "device_export_field", "device_export_format", "device_export_run", "device_export_cancel", "device_catalog_import_action",
                "device_compare_start", "device_compare_row", "device_compare_page", "device_completeness", "device_registration_origin", "device_inventory_wizard", "device_inventory_wizard_action", "device_private_note", "device_private_note_action", "device_place_label", "device_place_label_action", "device_review_date", "device_review_date_action", "device_maintenance", "device_maintenance_action", "device_relations", "device_relation_action", "device_purpose", "device_purpose_choice", "device_inventory_template", "device_inventory_template_action", "device_duplicate_candidate", "device_tools",
            "inactivity_action",
            "typed_field_edit", "typed_field_action",
                "cpu_history", "cpu_history_period", "memory_history", "memory_history_period", "volume_history",
                "memory_pressure_history", "memory_pressure_history_period",
                "thermal_snapshot",
                "battery_health",
                "battery_cycles",
                "energy_usage",
                "sleep_wake_history",
                "startup_duration",
                "pending_restart",
                "os_support",
                "filevault_status",
                "sip_status",
                "gatekeeper_status",
                "hardware_inventory_history",
            "driver_inventory_history",
            "installed_app_history",
            "startup_inventory_history",
            "service_inventory_history",
            "crash_summary",
                "smart_trend",
                    "volume_topology", "network_latency_series", "network_packet_loss_history", "network_jitter", "network_throughput", "network_link_speed", "network_dns_resolution", "network_dns_settings", "network_dns_comparison", "network_gateway_probe", "network_captive_portal", "network_ipv6_connectivity", "network_tls_certificate", "network_service_probe", "network_wifi_quality_history", "network_wifi_channel_summary", "network_wifi_connection_history", "network_proxy_status", "network_vpn_route_status", "network_endpoint_batch", "network_dnssec_check", "network_path_diagnostic", "network_check_sets", "network_external_ip_watch", "network_interface_traffic", "network_route_trace", "network_path_mtu",
                "network_route_lookup",
                "terminal_shell_timing",
                "terminal_shell_runner_diagnostic",
                "terminal_shell_late_results",
                "terminal_shell_approval_inbox",
            "terminal_shell_runbook",
                "terminal_shell_runbook_templates",
                "terminal_shell_metadata_export",
                "clock_drift",
                "system_summary_history",
                "system_summary_comparison",
                "system_summary_export",
                "system_summary_interval",
                "system_summary_thresholds",
                "system_summary_aggregate",
                "system_summary_uncertainty",
        }:
            raise ValueError("feature registry navigation renderer is not registered")
        required_grants = set(row["grants"])
        if role != "owner" and not required_grants.issubset(set(grants)):
            continue
        visible.append(dict(row))
    return visible


def find_visible_feature(
    registry: Mapping[str, Any], feature_id: str, *, role: str,
    grants: Collection[str],
) -> dict[str, Any] | None:
    """Resolve one detail row without paging assumptions or handler dispatch."""
    if role not in {"owner", "user", "guest"}:
        return None
    rows = registry.get("features", [])
    if not isinstance(rows, list):
        raise ValueError("feature registry has an invalid shape")
    for item in rows:
        if not isinstance(item, dict) or item.get("feature_id") != feature_id:
            continue
        allowed_roles = item.get("role", [])
        if role not in allowed_roles or not _grant_allows(item, role, grants):
            return None
        return dict(item)
    return None


def build_feature_page(
    registry: Mapping[str, Any],
    user_id: int,
    selected_device_id: str | None,
    page_cursor: int,
    *,
    role: str,
    device_ids: Collection[str],
    grants: Collection[str],
    registered_handlers: Collection[str] | Mapping[str, Any],
    page_size: int = 8,
    area: str | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """Build a role-filtered read-only page and mark, but never invoke, handlers.

    Callers must re-check authorization in their actual callback. Candidate or
    registry data can never publish MQTT messages or execute local code.
    """
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id must be a positive Telegram ID")
    if role not in {"owner", "user", "guest", "blocked"}:
        raise ValueError("unknown role")
    if not 1 <= int(page_size) <= 20:
        raise ValueError("page_size must be between 1 and 20")
    try:
        cursor = max(0, int(page_cursor))
    except (TypeError, ValueError):
        cursor = 0
    if role == "blocked":
        return {"state": "blocked", "user_id": user_id, "items": [], "page_cursor": 0,
                "has_previous": False, "has_next": False}
    if selected_device_id is not None and selected_device_id not in set(device_ids):
        return {"state": "stale_device", "user_id": user_id, "items": [], "page_cursor": 0,
                "has_previous": False, "has_next": False}

    rows = registry.get("features", [])
    if not isinstance(rows, list):
        raise ValueError("feature registry has an invalid shape")
    visible = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if area is not None and raw.get("area") != area:
            continue
        if platform is not None:
            supported_platforms = raw.get("platforms")
            if not isinstance(supported_platforms, list) or platform not in supported_platforms:
                continue
        if role not in raw.get("role", []):
            continue
        if not _grant_allows(raw, role, grants):
            continue
        item = dict(raw)
        handler_id = item.get("handler_id")
        is_registered = (
            handler_id in registered_handlers
            if isinstance(handler_id, str)
            else False
        )
        item["can_execute"] = bool(
            item.get("support_state") == "implemented"
            and is_registered
            and role != "guest"
            and selected_device_id is not None
        )
        visible.append(item)
    visible.sort(key=lambda item: (str(item.get("area", "")), str(item.get("feature_id", ""))))
    page_count = max(1, (len(visible) + int(page_size) - 1) // int(page_size))
    cursor = min(cursor, page_count - 1)
    start = cursor * int(page_size)
    return {
        "state": "ok",
        "user_id": user_id,
        "selected_device_id": selected_device_id,
        "page_cursor": cursor,
        "page_count": page_count,
        "has_previous": cursor > 0,
        "has_next": cursor + 1 < page_count,
        "items": visible[start:start + int(page_size)],
    }
