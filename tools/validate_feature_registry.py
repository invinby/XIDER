"""Validate the static XIDER Feature Registry without loading runtime code."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AREA_IDS = (
    "B01", "B02", "D01", "D02", "D03", "D04", "D05", "D06", "D07",
    "A01", "S01", "R01", "P01", "O01", "W01", "M01", "G01",
)
COMPONENTS = ("bot", "windows_agent", "macos_agent", "server", "site")
SUPPORT_STATES = {"planned", "implemented", "unsupported", "needs_device_test", "unknown"}
ROLE_IDS = {"owner", "user", "guest", "public"}
GRANT_KINDS = {"owner_only", "delegable", "public", "none"}
ACCEPTANCE_LEVELS = {"unit", "integration", "device", "live"}

TOP_LEVEL_FIELDS = {"schema_version", "baseline", "areas", "navigation", "features"}
BASELINE_FIELDS = {"repository", "commit"}
AREA_FIELDS = {"area_id", "label_id", "description"}
FEATURE_FIELDS = {
    "feature_id", "area", "label_id", "title", "description", "outcome", "platforms",
    "result_types", "component_min_versions", "role", "grant", "permissions", "support_state",
    "baseline_evidence", "handler_id", "test_refs", "acceptance_level",
}
OPTIONAL_FEATURE_FIELDS: set[str] = {"result_types"}
RESULT_TYPES = {"text", "image", "file", "status"}
EVIDENCE_FIELDS = {"path", "reference", "novelty_status"}
GRANT_FIELDS = {"kind", "key"}
VERSION_FIELDS = {"state", "minimum", "reason"}
NAVIGATION_FIELDS = {
    "page_id", "parent", "label_id", "callback", "roles", "grants",
    "platforms", "permissions", "support_state", "style", "presentation",
}
OPTIONAL_NAVIGATION_FIELDS = {"when"}
PLATFORMS = {"telegram", "windows", "macos", "server", "web"}

_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|token|api[_ -]?key)\s*[:=]\s*\S{12,}"),
)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_LABEL_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,100}$")
_HANDLER_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,100}$")
_FEATURE_ID = re.compile(r"^(?P<area>[A-Z][A-Z0-9]{1,3})-F(?P<number>\d{2})$")


def _err(errors: list[str], code: str, location: str, detail: str) -> None:
    errors.append(f"{code}: {location}: {detail}")


def _unknown_fields(
    value: dict[str, Any], allowed: set[str], errors: list[str], location: str
) -> None:
    for key in sorted(set(value) - allowed):
        _err(errors, "UNKNOWN_FIELD", location, f"unexpected field {key!r}")


def _check_secret_like(value: Any, errors: list[str], location: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _check_secret_like(child, errors, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_secret_like(child, errors, f"{location}[{index}]")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        _err(errors, "SECRET_LIKE", location, "secret-shaped value is forbidden")


def _repo_file(reference: str, errors: list[str], code: str, location: str) -> Path | None:
    candidate = (ROOT / reference).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        _err(errors, code, location, "reference escapes repository root")
        return None
    if not candidate.is_file():
        _err(errors, code, location, f"file does not exist: {reference}")
        return None
    return candidate


def _validate_version_decision(
    component: str, decision: Any, errors: list[str], location: str
) -> None:
    if not isinstance(decision, dict):
        _err(errors, "INVALID_COMPONENT_VERSION", location, "decision must be an object")
        return
    _unknown_fields(decision, VERSION_FIELDS, errors, location)
    state = decision.get("state")
    if state == "required":
        minimum = decision.get("minimum")
        if not isinstance(minimum, str) or not _SEMVER.fullmatch(minimum):
            _err(errors, "INVALID_COMPONENT_VERSION", location, "required component needs a semantic minimum version")
        if "reason" in decision:
            _err(errors, "INVALID_COMPONENT_VERSION", location, "required version must not include a decision reason")
    elif state in {"planned", "not_applicable"}:
        if decision.get("minimum") is not None:
            _err(errors, "INVALID_COMPONENT_VERSION", location, f"{state} decision must not claim a minimum version")
        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            _err(errors, "INVALID_COMPONENT_VERSION", location, f"{state} decision requires a reason")
    else:
        _err(errors, "INVALID_COMPONENT_VERSION", location, f"unknown decision for component {component!r}")


def _validate_feature(feature: Any, index: int, errors: list[str]) -> None:
    location = f"features[{index}]"
    if not isinstance(feature, dict):
        _err(errors, "INVALID_FEATURE", location, "feature must be an object")
        return
    _unknown_fields(feature, FEATURE_FIELDS, errors, location)
    for field in sorted(FEATURE_FIELDS - OPTIONAL_FEATURE_FIELDS - set(feature)):
        _err(errors, "MISSING_FIELD", location, f"required field {field!r} is missing")

    feature_id = feature.get("feature_id")
    match = _FEATURE_ID.fullmatch(feature_id) if isinstance(feature_id, str) else None
    if not match or match.group("area") not in AREA_IDS or not 1 <= int(match.group("number")) <= 30:
        _err(errors, "INVALID_ID", location, "feature_id must be one of the 30 IDs in an approved area")
    elif feature.get("area") != match.group("area"):
        _err(errors, "INVALID_ID", location, "feature_id prefix must match area")

    for field in ("label_id",):
        value = feature.get(field)
        if not isinstance(value, str) or not _LABEL_ID.fullmatch(value):
            _err(errors, "INVALID_FIELD", f"{location}.{field}", "must be a stable lower-case label ID")
    title = feature.get("title")
    if not isinstance(title, str) or not title.strip():
        _err(errors, "INVALID_FIELD", f"{location}.title", "must provide a short human-readable title")
    description = feature.get("description")
    if description is not None and (
        not isinstance(description, str) or not description.strip() or len(description) > 500
    ):
        _err(errors, "INVALID_FIELD", f"{location}.description", "must be non-empty text of at most 500 characters when provided")
    outcome = feature.get("outcome")
    if not isinstance(outcome, str) or not outcome.strip():
        _err(errors, "INVALID_FIELD", f"{location}.outcome", "must describe a user-visible outcome")

    result_types = feature.get("result_types")
    if result_types is not None and (
        not isinstance(result_types, list)
        or not result_types
        or any(not isinstance(item, str) or item not in RESULT_TYPES for item in result_types)
        or len(result_types) != len(set(result_types))
    ):
        _err(errors, "INVALID_FIELD", f"{location}.result_types", "must be a non-empty unique list of text, image, file or status")

    platforms = feature.get("platforms")
    if not isinstance(platforms, list) or not platforms or any(
        not isinstance(item, str) or item not in {"windows", "macos", "server", "telegram", "web"}
        for item in platforms
    ):
        _err(errors, "INVALID_FIELD", f"{location}.platforms", "must be a non-empty list of known platforms")

    versions = feature.get("component_min_versions")
    if not isinstance(versions, dict):
        _err(errors, "INVALID_COMPONENT_VERSION", f"{location}.component_min_versions", "must be an object")
    else:
        _unknown_fields(versions, set(COMPONENTS), errors, f"{location}.component_min_versions")
        for component in COMPONENTS:
            if component not in versions:
                _err(errors, "MISSING_FIELD", f"{location}.component_min_versions", f"missing decision for {component}")
            else:
                _validate_version_decision(
                    component, versions[component], errors, f"{location}.component_min_versions.{component}"
                )

    roles = feature.get("role")
    if not isinstance(roles, list) or not roles or any(role not in ROLE_IDS for role in roles):
        _err(errors, "INVALID_ROLE", f"{location}.role", "must list allowed roles; blocked is never a role")
    grant = feature.get("grant")
    if not isinstance(grant, dict):
        _err(errors, "INVALID_GRANT", f"{location}.grant", "must be an explicit grant decision")
    else:
        _unknown_fields(grant, GRANT_FIELDS, errors, f"{location}.grant")
        if grant.get("kind") not in GRANT_KINDS:
            _err(errors, "INVALID_GRANT", f"{location}.grant", "unknown grant kind")
        if grant.get("kind") == "owner_only" and roles != ["owner"]:
            _err(errors, "INVALID_GRANT", f"{location}.grant", "owner_only features must list only owner")
        if grant.get("kind") == "delegable" and not isinstance(grant.get("key"), str):
            _err(errors, "INVALID_GRANT", f"{location}.grant.key", "delegable grant requires a stable key")

    permissions = feature.get("permissions")
    if not isinstance(permissions, list) or any(not isinstance(item, str) or not item.strip() for item in permissions):
        _err(errors, "INVALID_PERMISSION", f"{location}.permissions", "must be a list of explicit permission IDs")
    elif len(permissions) != len(set(permissions)):
        _err(errors, "INVALID_PERMISSION", f"{location}.permissions", "permission IDs must be unique")

    state = feature.get("support_state")
    if state not in SUPPORT_STATES:
        _err(errors, "INVALID_STATE", f"{location}.support_state", "unsupported support state")
    handler = feature.get("handler_id")
    tests = feature.get("test_refs")
    if state == "implemented":
        if not isinstance(handler, str) or not _HANDLER_ID.fullmatch(handler):
            _err(errors, "INVALID_HANDLER", f"{location}.handler_id", "implemented feature requires a registered handler ID")
        if not isinstance(tests, list) or not tests:
            _err(errors, "MISSING_TEST", f"{location}.test_refs", "implemented feature requires test references")
    elif handler is not None and (not isinstance(handler, str) or not _HANDLER_ID.fullmatch(handler)):
        _err(errors, "INVALID_HANDLER", f"{location}.handler_id", "must be null until a handler is registered")

    if isinstance(tests, list):
        for test_index, test_ref in enumerate(tests):
            test_location = f"{location}.test_refs[{test_index}]"
            if not isinstance(test_ref, str) or "::" not in test_ref:
                _err(errors, "UNRESOLVED_TEST", test_location, "expected repo-relative path::test_name")
                continue
            file_ref, test_name = test_ref.split("::", 1)
            test_file = _repo_file(file_ref, errors, "UNRESOLVED_TEST", test_location)
            if test_file is not None and (not test_name or test_name not in test_file.read_text(encoding="utf-8")):
                _err(errors, "UNRESOLVED_TEST", test_location, "test name was not found in referenced file")

    evidence = feature.get("baseline_evidence")
    if not isinstance(evidence, list) or not evidence:
        _err(errors, "UNRESOLVED_EVIDENCE", f"{location}.baseline_evidence", "at least one baseline reference is required")
    else:
        for evidence_index, item in enumerate(evidence):
            evidence_location = f"{location}.baseline_evidence[{evidence_index}]"
            if not isinstance(item, dict):
                _err(errors, "UNRESOLVED_EVIDENCE", evidence_location, "evidence must be an object")
                continue
            _unknown_fields(item, EVIDENCE_FIELDS, errors, evidence_location)
            for field in sorted(EVIDENCE_FIELDS - set(item)):
                _err(errors, "MISSING_FIELD", evidence_location, f"required field {field!r} is missing")
            file_ref, reference = item.get("path"), item.get("reference")
            if not isinstance(file_ref, str) or not isinstance(reference, str) or not reference:
                _err(errors, "UNRESOLVED_EVIDENCE", evidence_location, "path and reference must be non-empty strings")
                continue
            source = _repo_file(file_ref, errors, "UNRESOLVED_EVIDENCE", evidence_location)
            if source is not None and reference not in source.read_text(encoding="utf-8"):
                _err(errors, "UNRESOLVED_EVIDENCE", evidence_location, "reference was not found in source file")
            if item.get("novelty_status") not in {"needs_novelty_audit", "confirmed_absent", "confirmed_overlap"}:
                _err(errors, "UNRESOLVED_EVIDENCE", evidence_location, "invalid novelty status")

    levels = feature.get("acceptance_level")
    if not isinstance(levels, list) or not levels or any(level not in ACCEPTANCE_LEVELS for level in levels):
        _err(errors, "INVALID_ACCEPTANCE", f"{location}.acceptance_level", "must name at least one known acceptance level")
    if state == "needs_device_test" and isinstance(levels, list) and "device" not in levels:
        _err(errors, "INVALID_ACCEPTANCE", f"{location}.acceptance_level", "needs_device_test requires device acceptance")


def _validate_navigation(rows: Any, errors: list[str]) -> None:
    if not isinstance(rows, list):
        _err(errors, "INVALID_NAVIGATION", "$.navigation", "navigation must be a list")
        return
    ids: list[str] = []
    routes: list[tuple[str, str]] = []
    for index, row in enumerate(rows):
        location = f"navigation[{index}]"
        if not isinstance(row, dict):
            _err(errors, "INVALID_NAVIGATION", location, "page must be an object")
            continue
        _unknown_fields(row, NAVIGATION_FIELDS | OPTIONAL_NAVIGATION_FIELDS, errors, location)
        for field in NAVIGATION_FIELDS - set(row):
            _err(errors, "INVALID_NAVIGATION", location, f"missing field {field!r}")
        page_id = row.get("page_id")
        callback = row.get("callback")
        if not isinstance(page_id, str) or not _LABEL_ID.fullmatch(page_id):
            _err(errors, "INVALID_NAVIGATION", f"{location}.page_id", "must be a stable page ID")
        else:
            ids.append(page_id)
        if not isinstance(callback, str) or not re.fullmatch(r"[a-z][a-z0-9:_-]{1,98}(?:\*)?", callback):
            _err(errors, "INVALID_NAVIGATION", f"{location}.callback", "must be a static callback identifier or a trailing device-ID template")
        for field in ("parent", "label_id"):
            if not isinstance(row.get(field), str) or not _LABEL_ID.fullmatch(row.get(field, "")):
                _err(errors, "INVALID_NAVIGATION", f"{location}.{field}", "must be a stable identifier")
        parent = row.get("parent")
        if isinstance(parent, str) and _LABEL_ID.fullmatch(parent) and isinstance(callback, str) and re.fullmatch(r"[a-z][a-z0-9:_-]{1,99}", callback):
            routes.append((parent, callback))
        for field, allowed in (("roles", ROLE_IDS - {"public"}), ("platforms", PLATFORMS)):
            values = row.get(field)
            if not isinstance(values, list) or not values or any(value not in allowed for value in values):
                _err(errors, "INVALID_NAVIGATION", f"{location}.{field}", "must be a non-empty list of known values")
        for field in ("grants", "permissions"):
            values = row.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or not _LABEL_ID.fullmatch(value) for value in values):
                _err(errors, "INVALID_NAVIGATION", f"{location}.{field}", "must be a list of stable identifiers")
        if row.get("support_state") not in SUPPORT_STATES:
            _err(errors, "INVALID_NAVIGATION", f"{location}.support_state", "must use a known support state")
        if row.get("style") not in {"primary", "success", "danger"}:
            _err(errors, "INVALID_NAVIGATION", f"{location}.style", "must use a supported Telegram button style")
        if row.get("presentation") not in {
            "nav", "versions", "capabilities", "device_tags", "device_tags_action", "device_purpose", "device_purpose_choice", "install_os", "devices_row", "devices_list_action", "all_device_action", "snapshot_detail_action", "snapshot_delete_action", "file_inbox", "file_inbox_action", "file_inbox_row", "home_devices", "home_server",
            "home_admin", "home_catalog", "home_about", "home_access_request", "permission_requests", "permission_request_row", "permission_request_decision", "screen_action", "camera_quality",
            "catalog_area", "catalog_action", "catalog_feature", "mic_duration", "microphone_level", "microphone_balance", "microphone_history", "audio_output_catalog", "audio_output_switch", "microphone_test", "microphone_preview",
            "catalog_result_type",
            "catalog_synonym", "catalog_synonym_cancel",
            "volume_level", "rotate_angle", "url_count", "url_count_action",
            "power_confirmation",
            "url_favorite_open", "url_favorite_delete", "guest_device_row",
            "admin_user_row", "admin_users_empty",
            "admin_user_role", "admin_user_block", "admin_user_action", "admin_user_role_history", "admin_user_grant_history", "admin_user_revoke_all_grants", "admin_user_pause_grants", "admin_user_grant_undo", "admin_user_permission_profiles", "admin_permission_profile_save", "admin_permission_profile_row", "admin_permission_profile_page", "admin_permission_profile_cancel", "admin_owner_invariant", "admin_rbac_snapshot", "admin_rbac_compare", "audit_filter", "audit_export", "audit_event_row",
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
            "terminal_shell_runbook_templates",
            "terminal_shell_metadata_export",
            "terminal_shell_runbook",
            "clock_drift",
            "network_jitter",
            "network_throughput",
                "system_summary_history",
                "system_summary_comparison",
                "system_summary_export",
                "system_summary_interval",
                "system_summary_thresholds",
                "system_summary_aggregate",
                "system_summary_uncertainty",
        }:
            _err(errors, "INVALID_NAVIGATION", f"{location}.presentation", "must name a registered static renderer")
        if "when" in row and row.get("when") not in {"awake", "standby"}:
            _err(errors, "INVALID_NAVIGATION", f"{location}.when", "must be awake or standby")
    if len(ids) != len(set(ids)):
        _err(errors, "DUPLICATE_NAVIGATION", "$.navigation", "page IDs must be unique")
    if len(routes) != len(set(routes)):
        _err(errors, "DUPLICATE_NAVIGATION", "$.navigation", "callbacks must be unique within each parent page")


def validate_feature_registry(path: Path) -> list[str]:
    """Return stable, non-secret-bearing validation errors for the registry at path."""
    registry_path = Path(path)
    try:
        raw = registry_path.read_text(encoding="utf-8")
    except OSError:
        return [f"JSON_PARSE: $: registry file cannot be read: {registry_path}"]
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"JSON_PARSE: $: malformed JSON at line {exc.lineno}, column {exc.colno}"]

    errors: list[str] = []
    _check_secret_like(document, errors)
    if not isinstance(document, dict):
        return [*errors, "INVALID_DOCUMENT: $: registry root must be an object"]
    _unknown_fields(document, TOP_LEVEL_FIELDS, errors, "$")
    for field in sorted(TOP_LEVEL_FIELDS - set(document)):
        _err(errors, "MISSING_FIELD", "$", f"required field {field!r} is missing")
    if document.get("schema_version") != 1:
        _err(errors, "INVALID_SCHEMA", "$.schema_version", "expected schema version 1")

    baseline = document.get("baseline")
    if not isinstance(baseline, dict):
        _err(errors, "INVALID_BASELINE", "$.baseline", "baseline must be an object")
    else:
        _unknown_fields(baseline, BASELINE_FIELDS, errors, "$.baseline")
        if baseline.get("repository") != "invinby/XIDER":
            _err(errors, "INVALID_BASELINE", "$.baseline.repository", "must identify the canonical repository")
        commit = baseline.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            _err(errors, "INVALID_BASELINE", "$.baseline.commit", "must be a full lowercase git commit SHA")

    areas = document.get("areas")
    if not isinstance(areas, list):
        _err(errors, "AREA_COUNT", "$.areas", "areas must be a list")
    else:
        area_ids = []
        for index, area in enumerate(areas):
            location = f"areas[{index}]"
            if not isinstance(area, dict):
                _err(errors, "INVALID_AREA", location, "area must be an object")
                continue
            _unknown_fields(area, AREA_FIELDS, errors, location)
            for field in AREA_FIELDS - set(area):
                _err(errors, "MISSING_FIELD", location, f"required field {field!r} is missing")
            area_id = area.get("area_id")
            if isinstance(area_id, str):
                area_ids.append(area_id)
            if not isinstance(area.get("label_id"), str) or not _LABEL_ID.fullmatch(area.get("label_id", "")):
                _err(errors, "INVALID_AREA", f"{location}.label_id", "must be a stable label ID")
            if not isinstance(area.get("description"), str) or not area.get("description", "").strip():
                _err(errors, "INVALID_AREA", f"{location}.description", "must describe the product area")
        if len(area_ids) != len(AREA_IDS) or set(area_ids) != set(AREA_IDS):
            _err(errors, "AREA_COUNT", "$.areas", f"expected exactly the approved {len(AREA_IDS)} area IDs")
        if len(area_ids) != len(set(area_ids)):
            _err(errors, "DUPLICATE_AREA", "$.areas", "area IDs must be unique")

    _validate_navigation(document.get("navigation"), errors)

    features = document.get("features")
    if not isinstance(features, list):
        _err(errors, "FEATURE_COUNT", "$.features", "features must be a list")
        return errors

    ids: list[str] = []
    counts = {area: 0 for area in AREA_IDS}
    for index, feature in enumerate(features):
        if isinstance(feature, dict) and isinstance(feature.get("feature_id"), str):
            feature_id = feature["feature_id"]
            ids.append(feature_id)
            prefix = feature_id.split("-F", 1)[0]
            if prefix in counts:
                counts[prefix] += 1
        _validate_feature(feature, index, errors)
    if len(ids) != len(set(ids)):
        _err(errors, "DUPLICATE_ID", "$.features", "feature IDs must be unique")
    if len(features) != len(AREA_IDS) * 30:
        _err(errors, "FEATURE_COUNT", "$.features", f"expected exactly {len(AREA_IDS) * 30} features")
    for area, count in counts.items():
        if count != 30:
            _err(errors, "AREA_FEATURE_COUNT", f"$.features[{area}]", f"expected 30 features, found {count}")
        expected = {f"{area}-F{number:02d}" for number in range(1, 31)}
        actual = {feature_id for feature_id in ids if feature_id.startswith(f"{area}-F")}
        if actual != expected:
            _err(errors, "AREA_FEATURE_IDS", f"$.features[{area}]", "IDs must contain each suffix F01 through F30 exactly once")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", nargs="?", type=Path, default=ROOT / "docs/features/feature-registry.json")
    args = parser.parse_args()
    errors = validate_feature_registry(args.registry)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Feature Registry is valid (510 unique features across 17 areas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
