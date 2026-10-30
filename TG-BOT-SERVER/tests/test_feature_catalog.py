from __future__ import annotations

from pathlib import Path
import json
import pytest

import feature_catalog
import xlex


ROOT = Path(__file__).resolve().parents[2]


def test_system_location_copy_is_distinct_from_the_explicit_ip_estimate():
    system_copy = xlex.render("geo_system", "xperson")
    ip_copy = xlex.render("geo_beta", "xperson")

    assert "систем" in system_copy.casefold()
    assert "ip" not in system_copy.casefold()
    assert "ip" in ip_copy.casefold()


def test_matrix_prank_copy_reports_terminal_launch_without_fullscreen_claim():
    button_labels = [xlex.render("prank_matrix_button", style) for style in xlex.STYLES]
    result_copy = [xlex.render("prank_matrix_unconfirmed", style) for style in xlex.STYLES]

    assert all("full" not in label.casefold() for label in button_labels)
    assert all("на весь экран" not in label.casefold() for label in button_labels)
    assert all(
        any(marker in text.casefold() for marker in ("не подтверж", "не подтверд", "не может подтвердить", "неизвест"))
        for text in result_copy
    )


def test_matrix_prank_requires_physical_device_acceptance_in_registry():
    registry = json.loads((ROOT / "docs" / "features" / "feature-registry.json").read_text(encoding="utf-8"))
    row = next(item for item in registry["navigation"] if item.get("page_id") == "prank.visual.prank.matrix")

    assert row["support_state"] == "needs_device_test"


def test_random_cursor_motion_is_not_listed_as_supported_on_macos():
    registry = json.loads((ROOT / "docs" / "features" / "feature-registry.json").read_text(encoding="utf-8"))
    row = next(item for item in registry["navigation"] if item.get("callback") == "cmd:prank_random_clicks")

    assert row["platforms"] == ["windows"]


def _registry():
    return {
        "areas": [{"area_id": "D01", "label_id": "feature.area.d01.title", "description": "System"}],
        "features": [
            {
                "feature_id": "D01-F01", "area": "D01", "label_id": "feature.d01.f01.title",
                "title": "Battery condition", "outcome": "Know the health of the power cell",
                "description": "Indexed detail: estimate battery reserve and chemistry health",
                "platforms": ["windows"], "component_min_versions": {}, "role": ["owner", "user"],
                "grant": {"kind": "delegable", "key": "battery_read"}, "permissions": [],
                "support_state": "planned", "baseline_evidence": [], "handler_id": None,
                "test_refs": [], "acceptance_level": ["unit"],
            },
            {
                "feature_id": "D01-F02", "area": "D01", "label_id": "feature.d01.f02.title",
                "title": "Disk map", "outcome": "See storage layout", "platforms": ["macos"],
                "component_min_versions": {}, "role": ["owner"],
                "grant": {"kind": "owner_only", "key": None}, "permissions": [],
                "support_state": "implemented", "baseline_evidence": [],
                "handler_id": "feature.d01.f02", "test_refs": ["test_feature_catalog.py::test_exact_and_outcome_search_are_distinct"],
                "acceptance_level": ["unit"],
            },
        ],
    }


def test_stale_grant_button_labels_are_registered_and_stable():
    assert feature_catalog.button_text("admin_stale_grants") == "🔎 Права на отсутствующие устройства"
    assert feature_catalog.button_text("stale_grants_refresh") == "↻ Перепроверить"
    assert feature_catalog.button_text("access_review") == "🧾 Обзор ролей и прав"
    assert feature_catalog.button_text("access_review_refresh") == "↻ Снять новый срез"
    assert feature_catalog.button_text("access_review_previous") == "◀ Предыдущая страница"
    assert feature_catalog.button_text("access_review_next") == "Следующая страница ▶"


def test_exact_and_outcome_search_are_distinct():
    registry = _registry()

    assert [item["feature_id"] for item in feature_catalog.search_features(registry, "power cell", mode="exact")] == []
    assert [item["feature_id"] for item in feature_catalog.search_features(registry, "power cell", mode="outcome")] == ["D01-F01"]
    assert [item["feature_id"] for item in feature_catalog.search_features(registry, "battery condition", mode="exact")] == ["D01-F01"]
    assert [item["feature_id"] for item in feature_catalog.search_features(registry, "chemistry health", mode="exact")] == ["D01-F01"]


def test_result_type_filter_uses_only_explicit_registry_metadata():
    registry = _registry()
    registry["features"][0]["result_types"] = ["text", "status"]
    registry["features"][1]["result_types"] = ["image"]
    registry["features"].append({
        **registry["features"][0], "feature_id": "D01-F03", "title": "Archive export",
    })
    registry["features"][-1].pop("result_types")

    assert [row["feature_id"] for row in feature_catalog.filter_features_by_result_type(registry, "image")] == ["D01-F02"]
    assert [row["feature_id"] for row in feature_catalog.filter_features_by_result_type(registry, "status")] == ["D01-F01"]
    assert [row["feature_id"] for row in feature_catalog.filter_features_by_result_type(registry, "file")] == []


def test_result_type_filter_rejects_unknown_type_and_ignores_malformed_metadata():
    registry = _registry()
    registry["features"][0]["result_types"] = ["text", "secret"]

    assert feature_catalog.filter_features_by_result_type(registry, "text") == []
    with pytest.raises(ValueError, match="result type"):
        feature_catalog.filter_features_by_result_type(registry, "secret")
    with pytest.raises(ValueError, match="result type"):
        feature_catalog.filter_features_by_result_type(registry, None)


def test_permission_filter_keeps_only_no_permission_or_freshly_granted_os_requirements():
    registry = {"features": [
        {"feature_id": "plain", "permissions": []},
        {"feature_id": "camera", "permissions": ["camera"]},
        {"feature_id": "mic", "permissions": ["microphone"]},
        {"feature_id": "location", "permissions": ["one_shot_location_consent"]},
        # Role and owner-confirmation requirements remain independent RBAC gates.
        {"feature_id": "rbac", "permissions": ["owner_role_or_explicit_grant"]},
    ]}

    assert [row["feature_id"] for row in feature_catalog.filter_features_by_permission_state(
        registry,
        {"camera": "granted", "microphone": "denied", "location": "granted"},
    )] == ["plain", "camera", "location", "rbac"]


def test_permission_filter_fails_closed_for_missing_unknown_or_malformed_status():
    registry = {"features": [
        {"feature_id": "camera", "permissions": ["camera"]},
        {"feature_id": "screen", "permissions": ["screen_recording"]},
        {"feature_id": "bad-metadata", "permissions": "camera"},
        {"feature_id": "plain", "permissions": []},
    ]}

    assert [row["feature_id"] for row in feature_catalog.filter_features_by_permission_state(
        registry, {"camera": "maybe", "screen_recording": "granted"},
    )] == ["screen", "plain"]


def test_access_filter_uses_role_grants_platform_and_supported_state():
    registry = {"features": [
        {"feature_id": "owner-win", "role": ["owner"],
         "grant": {"kind": "owner_only", "key": None}, "platforms": ["windows"],
         "support_state": "implemented"},
        {"feature_id": "delegated-win", "role": ["user"],
         "grant": {"kind": "delegable", "key": "cmd:status"}, "platforms": ["windows"],
         "support_state": "needs_device_test"},
        {"feature_id": "public-mac", "role": ["guest", "user"],
         "grant": {"kind": "public", "key": None}, "platforms": ["macos"],
         "support_state": "implemented"},
        {"feature_id": "planned-win", "role": ["user"],
         "grant": {"kind": "delegable", "key": "cmd:status"}, "platforms": ["windows"],
         "support_state": "planned"},
    ]}

    assert [row["feature_id"] for row in feature_catalog.filter_features_for_access(
        registry, role="user", grants={"cmd:status"}, platform="windows",
    )] == ["delegated-win"]
    assert [row["feature_id"] for row in feature_catalog.filter_features_for_access(
        registry, role="guest", grants=set(), platform="macos",
    )] == ["public-mac"]
    assert [row["feature_id"] for row in feature_catalog.filter_features_for_access(
        registry, role="blocked", grants={"cmd:status"}, platform="windows",
    )] == []


def test_access_filter_unknown_platform_and_missing_grants_fail_closed():
    registry = {"features": [{
        "feature_id": "delegated", "role": ["user"],
        "grant": {"kind": "delegable", "key": "cmd:status"},
        "platforms": ["windows"], "support_state": "implemented",
    }]}

    assert feature_catalog.filter_features_for_access(
        registry, role="user", grants=set(), platform="windows",
    ) == []
    assert feature_catalog.filter_features_for_access(
        registry, role="user", grants={"cmd:status"}, platform="unknown",
    ) == []


def test_fuzzy_search_corrects_typos_only_in_identity_and_ranks_closest_title():
    registry = _registry()
    registry["features"].append({
        **registry["features"][0], "feature_id": "D01-F03", "title": "Battery cycles",
        "outcome": "Inspect charging history",
    })

    rows = feature_catalog.search_features(registry, "batteri condition", mode="fuzzy")

    assert [row["feature_id"] for row in rows] == ["D01-F01"]
    assert feature_catalog.search_features(registry, "batteri", mode="exact") == []
    assert feature_catalog.search_features(registry, "power cell", mode="fuzzy") == []


def test_fuzzy_search_requires_every_query_token_and_ignores_short_noise():
    registry = _registry()

    assert feature_catalog.search_features(registry, "disk batery", mode="fuzzy") == []
    assert feature_catalog.search_features(registry, "ab", mode="fuzzy") == []
    assert feature_catalog.search_features(registry, "!!!", mode="fuzzy") == []


def test_exact_search_uses_owner_synonyms_without_mixing_outcome_search():
    registry = _registry()
    aliases = {"D01-F01": ["my drawer health"]}

    assert feature_catalog.search_features(registry, "drawer", mode="exact") == []
    assert [row["feature_id"] for row in feature_catalog.search_features(
        registry, "drawer", mode="exact", synonyms=aliases,
    )] == ["D01-F01"]
    assert feature_catalog.search_features(registry, "drawer", mode="outcome", synonyms=aliases) == []
    assert feature_catalog.search_features(registry, "drawer", mode="fuzzy", synonyms=aliases) == []


def test_feature_page_filters_role_device_and_registered_handlers():
    registry = _registry()

    owner_page = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id="mac-a", page_cursor=0,
        role="owner", device_ids={"mac-a"}, grants=set(), registered_handlers={"feature.d01.f02"},
    )
    user_page = feature_catalog.build_feature_page(
        registry, user_id=200, selected_device_id="mac-a", page_cursor=0,
        role="user", device_ids={"mac-a"}, grants={"battery_read"}, registered_handlers={"feature.d01.f02"},
    )

    assert [item["feature_id"] for item in owner_page["items"]] == ["D01-F01", "D01-F02"]
    assert [item["can_execute"] for item in owner_page["items"]] == [False, True]
    assert [item["feature_id"] for item in user_page["items"]] == ["D01-F01"]
    assert user_page["items"][0]["can_execute"] is False


def test_feature_page_can_filter_to_selected_device_platform_without_changing_grants():
    registry = _registry()

    mac_page = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id="mac-a", page_cursor=0,
        role="owner", device_ids={"mac-a"}, grants=set(), registered_handlers=set(),
        platform="macos",
    )
    windows_page = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id="win-a", page_cursor=0,
        role="owner", device_ids={"win-a"}, grants=set(), registered_handlers=set(),
        platform="windows",
    )

    assert [item["feature_id"] for item in mac_page["items"]] == ["D01-F02"]
    assert [item["feature_id"] for item in windows_page["items"]] == ["D01-F01"]
    assert mac_page["items"][0]["can_execute"] is False


def test_feature_page_unknown_platform_does_not_claim_platform_support():
    registry = _registry()

    page = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id="linux-a", page_cursor=0,
        role="owner", device_ids={"linux-a"}, grants=set(), registered_handlers=set(),
        platform="linux",
    )

    assert page["items"] == []


def test_feature_page_rejects_stale_device_and_clamps_cursor():
    registry = _registry()

    stale = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id="removed", page_cursor=0,
        role="owner", device_ids=set(), grants=set(), registered_handlers=set(),
    )
    page = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id=None, page_cursor=99,
        role="owner", device_ids=set(), grants=set(), registered_handlers=set(), page_size=1,
    )

    assert stale["state"] == "stale_device"
    assert stale["items"] == []
    assert page["page_cursor"] == 1
    assert page["has_previous"] is True
    assert page["has_next"] is False


def test_feature_page_never_executes_metadata_handler():
    called = []
    registry = _registry()
    page = feature_catalog.build_feature_page(
        registry, user_id=100, selected_device_id="mac-a", page_cursor=0,
        role="owner", device_ids={"mac-a"}, grants=set(),
        registered_handlers={"feature.d01.f02": lambda: called.append(True)},
    )

    assert page["items"][1]["can_execute"] is True
    assert called == []


def test_detail_lookup_is_not_limited_by_page_size_and_enforces_grants():
    registry = _registry()
    registry["features"].extend(
        {
            **registry["features"][0],
            "feature_id": f"D01-F{index:02d}",
            "title": f"Feature {index}",
        }
        for index in range(3, 31)
    )

    assert feature_catalog.find_visible_feature(
        registry, "D01-F30", role="user", grants={"battery_read"},
    )["title"] == "Feature 30"
    assert feature_catalog.find_visible_feature(
        registry, "D01-F30", role="guest", grants={"battery_read"},
    ) is None
    assert feature_catalog.find_visible_feature(
        registry, "D01-F30", role="user", grants=set(),
    ) is None


def test_availability_explainer_reports_independent_role_grant_os_and_permission_blockers():
    feature = {
        "feature_id": "D02-F14", "role": ["owner", "user"],
        "grant": {"kind": "delegable", "key": "camera_capture"},
        "platforms": ["macos"], "permissions": ["camera"],
        "support_state": "needs_device_test",
    }

    assert feature_catalog.explain_availability(
        feature, role="guest", grants=set(), platform="windows",
        permission_states={"camera": "denied"},
    ) == [
        {"kind": "role", "state": "blocked"},
        {"kind": "grant", "state": "missing"},
        {"kind": "os", "state": "unsupported"},
        {"kind": "permission", "id": "camera", "state": "denied"},
    ]


def test_availability_explainer_keeps_unobserved_permission_unknown_and_owner_bypasses_grant():
    feature = {
        "feature_id": "D02-F14", "role": ["owner", "user"],
        "grant": {"kind": "delegable", "key": "camera_capture"},
        "platforms": ["macos"], "permissions": ["camera"],
        "support_state": "needs_device_test",
    }

    assert feature_catalog.explain_availability(
        feature, role="owner", grants=set(), platform="macos", permission_states={},
    ) == [{"kind": "permission", "id": "camera", "state": "unknown"}]


def test_search_rejects_unknown_mode():
    with pytest.raises(ValueError):
        feature_catalog.search_features(_registry(), "x", mode="similar")


def test_navigation_registry_filters_by_parent_role_grant_and_platform():
    registry = {
        "navigation": [
            {
                "page_id": "device.system", "parent": "device", "label_id": "system",
                "callback": "cat:system", "roles": ["owner", "user"],
                "grants": [], "platforms": ["windows", "macos"],
                "permissions": [], "support_state": "implemented", "style": "primary", "presentation": "nav",
            },
            {
                "page_id": "device.audit", "parent": "device", "label_id": "audit",
                "callback": "cat:audit", "roles": ["owner"],
                "grants": ["audit_read"], "platforms": ["windows"],
                "permissions": [], "support_state": "implemented", "style": "primary", "presentation": "nav",
            },
            {
                "page_id": "server.metrics", "parent": "server", "label_id": "metrics",
                "callback": "server:metrics", "roles": ["owner"],
                "grants": [], "platforms": ["server"],
                "permissions": [], "support_state": "planned", "style": "primary", "presentation": "nav",
            },
        ]
    }

    owner_pages = feature_catalog.visible_navigation_pages(
        registry, parent="device", role="owner", grants={"audit_read"}, platform="windows",
    )
    user_pages = feature_catalog.visible_navigation_pages(
        registry, parent="device", role="user", grants={"audit_read"}, platform="windows",
    )
    mac_pages = feature_catalog.visible_navigation_pages(
        registry, parent="device", role="owner", grants={"audit_read"}, platform="macos",
    )
    blocked_pages = feature_catalog.visible_navigation_pages(
        registry, parent="device", role="blocked", grants={"audit_read"}, platform="windows",
    )

    assert [page["page_id"] for page in owner_pages] == ["device.system", "device.audit"]
    assert [page["page_id"] for page in user_pages] == ["device.system"]
    assert [page["page_id"] for page in mac_pages] == ["device.system"]
    assert blocked_pages == []


def test_catalog_area_navigation_supports_static_catalog_renderers():
    registry = {
        "navigation": [
            {
                "page_id": "catalog.search.exact", "parent": "catalog",
                "label_id": "catalog.search.exact", "callback": "catalog:search:exact",
                "roles": ["owner", "user", "guest"], "grants": [],
                "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                "style": "primary", "presentation": "catalog_action",
            },
            {
                "page_id": "catalog.area.b01", "parent": "catalog",
                "label_id": "feature.area.b01.title", "callback": "catalog:area:B01:*",
                "roles": ["owner", "user", "guest"], "grants": [],
                "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                "style": "primary", "presentation": "catalog_area",
            },
        ]
    }

    visible = feature_catalog.visible_navigation_pages(
        registry, parent="catalog", role="guest", grants=set(), platform="telegram",
    )

    assert [row["page_id"] for row in visible] == ["catalog.search.exact", "catalog.area.b01"]
    assert feature_catalog.visible_navigation_pages(
        registry, parent="catalog", role="blocked", grants=set(), platform="telegram",
    ) == []


def test_navigation_registry_filters_runtime_state_without_dispatching():
    registry = {
        "navigation": [
            {
                "page_id": "power.wake", "parent": "power", "label_id": "wake",
                "callback": "cmd:wake", "roles": ["owner"], "grants": [],
                "platforms": ["windows", "macos"], "permissions": [],
                "support_state": "implemented", "style": "success",
                "presentation": "screen_action", "when": "standby",
            },
            {
                "page_id": "power.standby", "parent": "power", "label_id": "standby",
                "callback": "cmd:standby_sleep", "roles": ["owner"], "grants": [],
                "platforms": ["windows", "macos"], "permissions": [],
                "support_state": "implemented", "style": "primary",
                "presentation": "screen_action", "when": "awake",
            },
        ]
    }

    sleeping = feature_catalog.visible_navigation_pages(
        registry, parent="power", role="owner", grants=set(), platform="macos", state="standby",
    )
    awake = feature_catalog.visible_navigation_pages(
        registry, parent="power", role="owner", grants=set(), platform="macos", state="awake",
    )

    assert [item["callback"] for item in sleeping] == ["cmd:wake"]
    assert [item["callback"] for item in awake] == ["cmd:standby_sleep"]


def test_navigation_registry_fails_closed_for_invalid_rows_and_unknown_role():
    registry = {"navigation": [{"page_id": "device.system"}, None]}

    assert feature_catalog.visible_navigation_pages(
        registry, parent="device", role="blocked", grants=set(), platform="windows",
    ) == []
    with pytest.raises(ValueError, match="navigation"):
        feature_catalog.visible_navigation_pages(
            registry, parent="device", role="owner", grants=set(), platform="windows",
        )
