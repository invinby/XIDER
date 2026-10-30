from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import validate_feature_registry as feature_registry
from validate_feature_registry import validate_feature_registry
from build_feature_registry import build_feature_registry


ROOT = Path(__file__).resolve().parents[1]
SPEC = "docs/superpowers/specs/2026-10-14-tarped-capability-expansion-design.md"
AREA_IDS = (
    "B01", "B02", "D01", "D02", "D03", "D04", "D05", "D06", "D07",
    "A01", "S01", "R01", "P01", "O01", "W01", "M01", "G01",
)
COMPONENTS = ("bot", "windows_agent", "macos_agent", "server", "site")


def valid_registry() -> dict:
    areas = [
        {"area_id": area, "label_id": f"feature.area.{area.lower()}", "description": area}
        for area in AREA_IDS
    ]
    features = []
    for area in AREA_IDS:
        for number in range(1, 31):
            feature_id = f"{area}-F{number:02d}"
            features.append(
                {
                    "feature_id": feature_id,
                    "area": area,
                    "label_id": f"feature.{area.lower()}.f{number:02d}.title",
                    "title": f"Feature {feature_id}",
                    "description": f"Description of {feature_id}",
                    "outcome": f"User outcome for {feature_id}",
                    "platforms": ["windows", "macos"],
                    "component_min_versions": {
                        component: {
                            "state": "planned",
                            "minimum": None,
                            "reason": "not implemented at the baseline",
                        }
                        for component in COMPONENTS
                    },
                    "role": ["owner"],
                    "grant": {"kind": "owner_only", "key": None},
                    "permissions": [],
                    "support_state": "planned",
                    "baseline_evidence": [
                        {
                            "path": SPEC,
                            "reference": feature_id,
                            "novelty_status": "needs_novelty_audit",
                        }
                    ],
                    "handler_id": None,
                    "test_refs": [],
                    "acceptance_level": ["unit", "device"],
                }
            )
    return {
        "schema_version": 1,
        "baseline": {
            "repository": "invinby/XIDER",
            "commit": "6d20370a06eb157428627f9002852514615e8ee2",
        },
        "areas": areas,
        "navigation": [
            {
                "page_id": f"device.{key}", "parent": "device", "label_id": key,
                "callback": f"cat:{key}", "roles": ["owner", "user"],
                "grants": [], "platforms": ["windows", "macos"],
                "permissions": [], "support_state": "implemented", "style": "primary",
                "presentation": "nav",
            }
            for key in ("system", "screen", "files", "network", "terminal", "power")
        ],
        "features": features,
    }


def write_registry(tmp_path: Path, data: dict | str) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def has_error(errors: list[str], code: str) -> bool:
    return any(error.startswith(f"{code}:") for error in errors)


def test_feature_registry_validator_is_available_as_a_standalone_tool() -> None:
    """The registry gate must be callable without importing bot or agent code."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import validate_feature_registry as v; "
            "assert callable(v.validate_feature_registry); "
            "assert not {'bot', 'xgent_wds', 'xgent_mcs'} & sys.modules.keys()",
        ],
        cwd=ROOT / "tools",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_d03_f27_staged_inbox_is_linked_to_its_owner_scoped_implementation() -> None:
    registry = json.loads((ROOT / "docs/features/feature-registry.json").read_text(encoding="utf-8"))
    feature = next(row for row in registry["features"] if row["feature_id"] == "D03-F27")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d03.f27.files.owner_staged_inbox"
    assert {
        "TG-BOT-SERVER/tests/test_file_inbox.py::test_staged_file_is_owner_only_and_returns_metadata_not_storage_path",
        "TG-BOT-SERVER/tests/test_file_inbox.py::test_staged_file_limits_expiry_and_owner_scoped_delete",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_file_inbox_is_owner_only_and_uses_registered_actions",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_staged_file_placement_requires_preview_and_explicit_confirmation",
    }.issubset(set(feature["test_refs"]))


def test_feature_registry_accepts_optional_search_description(tmp_path: Path) -> None:
    data = valid_registry()
    data["features"][0]["description"] = "Indexed detail terms for the feature"

    assert validate_feature_registry(write_registry(tmp_path, data)) == []


def test_terminal_runbook_template_navigation_renderer_is_registered(tmp_path: Path) -> None:
    data = valid_registry()
    data["navigation"].append(
        {
            "page_id": "terminal.shell_runbook_templates",
            "parent": "device.terminal",
            "label_id": "feature.d05.f26.title",
            "callback": "cmd:shell_runbook_templates",
            "roles": ["owner"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "terminal_shell_runbook_templates",
        }
    )

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert not has_error(errors, "INVALID_NAVIGATION")


def test_device_tools_navigation_renderer_is_registered(tmp_path: Path) -> None:
    data = valid_registry()
    data["navigation"].append(
        {
            "page_id": "device.tools", "parent": "device", "label_id": "device_tools_button",
            "callback": "device:tools", "roles": ["owner", "user"],
            "grants": [], "platforms": ["windows", "macos"], "permissions": [],
            "support_state": "implemented", "style": "primary", "presentation": "device_tools",
        }
    )

    assert validate_feature_registry(write_registry(tmp_path, data)) == []


def test_d05_f26_builder_evidence_resolves_and_navigation_validates(tmp_path: Path) -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    feature = next(row for row in registry["features"] if row["feature_id"] == "D05-F26")
    navigation = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_runbook_templates")

    assert feature["baseline_evidence"][0]["reference"] == "D05-F26"
    assert feature["support_state"] == "needs_device_test"
    assert navigation["presentation"] == "terminal_shell_runbook_templates"
    assert validate_feature_registry(write_registry(tmp_path, registry)) == []


def test_feature_registry_validates_explicit_result_type_metadata(tmp_path: Path) -> None:
    data = valid_registry()
    data["features"][0]["result_types"] = ["text", "status"]

    assert validate_feature_registry(write_registry(tmp_path, data)) == []

    data["features"][0]["result_types"] = ["secret"]
    errors = validate_feature_registry(write_registry(tmp_path, data))
    assert has_error(errors, "INVALID_FIELD")


def test_catalog_area_screen_routes_are_declared_in_the_registry() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    pages = [row for row in registry["navigation"] if row["parent"] == "catalog"]

    assert {row["page_id"] for row in pages} == {
        "catalog.filter.result", "catalog.filter.permission", "catalog.filter.access", "catalog.search.exact", "catalog.search.outcome", "catalog.search.fuzzy", "catalog.home",
        *(f"catalog.area.{area.lower()}" for area in AREA_IDS),
    }
    area_pages = [row for row in pages if row["presentation"] == "catalog_area"]
    assert len(area_pages) == 17
    assert {row["callback"] for row in area_pages} == {"catalog:area:*"}
    page_controls = [row for row in registry["navigation"] if row["parent"] == "catalog.area.page"]
    assert {row["page_id"] for row in page_controls} == {
        "catalog.page.previous", "catalog.page.next", "catalog.page.sections", "catalog.page.home",
    }
    detail_controls = [
        row for row in registry["navigation"]
        if row["parent"] == "catalog.feature.detail"
    ]
    assert {row["page_id"] for row in detail_controls} == {
        "catalog.feature.back", "catalog.feature.home", "catalog.feature.synonym.add",
    }
    synonym_action = next(row for row in detail_controls if row["page_id"] == "catalog.feature.synonym.add")
    assert synonym_action["roles"] == ["owner"]
    assert synonym_action["callback"] == "catalog:synonym:add:*"
    synonym_cancel = next(row for row in registry["navigation"] if row["page_id"] == "catalog.feature.synonym.cancel")
    assert synonym_cancel["parent"] == "catalog.feature.synonym.prompt"
    assert synonym_cancel["roles"] == ["owner"]
    search_controls = [
        row for row in registry["navigation"]
        if row["parent"] in {"catalog.search.prompt", "catalog.search.results", "catalog.filter.results"}
    ]
    assert {row["page_id"] for row in search_controls} == {
        "catalog.search.prompt.sections", "catalog.search.prompt.home",
        "catalog.search.results.sections", "catalog.search.results.home",
        "catalog.search.results.feature",
        "catalog.filter.results.sections", "catalog.filter.results.home",
        "catalog.filter.results.feature",
    }
    result_type_pages = [row for row in registry["navigation"] if row["parent"] == "catalog.filter.types"]
    assert {row["page_id"] for row in result_type_pages} == {
        "catalog.filter.result.text", "catalog.filter.result.image",
        "catalog.filter.result.file", "catalog.filter.result.status",
        "catalog.filter.types.sections", "catalog.filter.types.home",
    }


def test_manual_install_os_picker_routes_are_declared_and_owner_only() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    pages = [row for row in registry["navigation"] if row["parent"] == "install.os"]

    assert {row["page_id"] for row in pages} == {
        "install.os.windows", "install.os.macos", "install.os.back",
    }
    assert {row["callback"] for row in pages} == {
        "install:windows", "install:macos", "menu:devices",
    }
    assert all(row["roles"] == ["owner"] for row in pages)
    assert all(row["presentation"] == "install_os" for row in pages)


def test_all_devices_actions_are_declared_owner_only_with_fixed_renderers() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    pages = [row for row in registry["navigation"] if row["parent"] == "all.devices"]

    assert [row["page_id"] for row in pages] == [
        "all.status", "all.screenshot", "all.location", "all.lock",
        "all.mute", "all.stop", "all.back", "all.home",
    ]
    assert all(row["roles"] == ["owner"] for row in pages)
    assert all(row["platforms"] == ["telegram"] for row in pages)
    assert all(row["presentation"] == "all_device_action" for row in pages)


def test_response_navigation_routes_are_declared_for_supported_roles() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    pages = [row for row in registry["navigation"] if row["parent"] == "device.response"]

    assert [(row["page_id"], row["callback"]) for row in pages] == [
        ("device.response.back", "back:device"),
        ("device.response.home", "menu:main"),
    ]
    assert all(row["roles"] == ["owner", "user", "guest"] for row in pages)
    assert all(row["presentation"] == "nav" for row in pages)


def test_saved_snapshot_detail_and_delete_confirmation_routes_are_declared() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    detail = [row for row in registry["navigation"] if row["parent"] == "catalog.snapshot.detail"]
    confirm = [row for row in registry["navigation"] if row["parent"] == "catalog.snapshot.delete.confirm"]

    assert [(row["page_id"], row["callback"]) for row in detail] == [
        ("catalog.snapshot.detail.delete", "catalog:snapshot:delete:*"),
        ("catalog.snapshot.detail.back", "catalog:snapshot:list"),
        ("catalog.snapshot.detail.home", "menu:main"),
    ]
    assert [(row["page_id"], row["callback"]) for row in confirm] == [
        ("catalog.snapshot.delete.confirm", "catalog:snapshot:delete:confirm:*"),
        ("catalog.snapshot.delete.cancel", "catalog:snapshot:view:*"),
        ("catalog.snapshot.delete.home", "menu:main"),
    ]
    assert all(row["roles"] == ["owner"] for row in detail + confirm)


def test_staged_file_inbox_routes_are_owner_only_and_non_executable() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    pages = [row for row in registry["navigation"] if row["page_id"].startswith("files.inbox")]

    assert {row["page_id"] for row in pages} == {
        "files.inbox", "files.inbox.upload", "files.inbox.row", "files.inbox.back", "files.inbox.home",
        "files.inbox.detail.place", "files.inbox.detail.delete", "files.inbox.detail.back", "files.inbox.detail.home",
        "files.inbox.place.confirm", "files.inbox.place.cancel", "files.inbox.place.home",
        "files.inbox.delete.confirm", "files.inbox.delete.cancel", "files.inbox.delete.home",
    }
    assert all(row["roles"] == ["owner"] for row in pages)
    assert all(row["support_state"] == "implemented" for row in pages)
    assert all(row["callback"].startswith(("files:inbox", "cat:files", "menu:main")) for row in pages)


def test_device_list_controls_are_declared_with_owner_and_shared_roles() -> None:
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    pages = [row for row in registry["navigation"] if row["parent"] == "devices"]
    by_id = {row["page_id"]: row for row in pages}

    assert set(by_id) == {
        "devices.row", "devices.search", "devices.filter_os", "devices.filter_heartbeat", "devices.filter_version", "devices.filter_capability", "devices.filter_inventory_age", "devices.saved_sets", "devices.collections", "devices.saved_filters", "devices.sort", "devices.order", "devices.pinned", "devices.activity_history", "devices.export", "devices.import", "devices.archive", "devices.all",
        "devices.add", "devices.install", "devices.blocked", "devices.clear",
        "devices.refresh", "devices.home",
    }
    assert by_id["devices.row"]["callback"] == "dev:*"
    assert by_id["devices.row"]["roles"] == ["owner", "user", "guest"]
    assert by_id["devices.search"]["callback"] == "devices:search"
    assert by_id["devices.search"]["roles"] == ["owner", "user"]
    assert by_id["devices.filter_os"]["callback"] == "devices:filter-os"
    assert by_id["devices.filter_os"]["roles"] == ["owner"]
    assert by_id["devices.filter_heartbeat"]["callback"] == "devices:filter-heartbeat"
    assert by_id["devices.filter_heartbeat"]["roles"] == ["owner"]
    assert by_id["devices.filter_version"]["callback"] == "devices:filter-version"
    assert by_id["devices.filter_version"]["roles"] == ["owner"]
    assert by_id["devices.filter_capability"]["callback"] == "devices:filter-capability"
    assert by_id["devices.filter_capability"]["roles"] == ["owner"]
    assert by_id["devices.filter_inventory_age"]["callback"] == "devices:filter-inventory-age"
    assert by_id["devices.filter_inventory_age"]["roles"] == ["owner"]
    assert by_id["devices.saved_sets"]["callback"] == "catalog:snapshot:list"
    assert by_id["devices.collections"]["callback"] == "catalog:collection:list"
    assert by_id["devices.collections"]["roles"] == ["owner"]
    assert by_id["devices.saved_filters"]["callback"] == "catalog:saved-filter:list"
    assert by_id["devices.saved_filters"]["roles"] == ["owner"]
    assert by_id["devices.sort"]["callback"] == "devices:sort"
    assert by_id["devices.sort"]["roles"] == ["owner"]
    assert by_id["devices.pinned"]["callback"] == "catalog:pinned:list"
    assert by_id["devices.pinned"]["roles"] == ["owner"]
    assert by_id["devices.activity_history"]["callback"] == "catalog:inactivity:list"
    assert by_id["devices.export"]["callback"] == "catalog:export"
    assert by_id["devices.export"]["roles"] == ["owner"]
    assert by_id["devices.archive"]["callback"] == "catalog:archive:list"
    assert by_id["devices.archive"]["roles"] == ["owner"]
    assert {page_id: by_id[page_id]["style"] for page_id in by_id} == {
        "devices.row": "success", "devices.search": "primary", "devices.filter_os": "primary", "devices.filter_heartbeat": "primary", "devices.filter_version": "primary", "devices.filter_capability": "primary", "devices.filter_inventory_age": "primary", "devices.saved_sets": "primary", "devices.collections": "primary", "devices.saved_filters": "primary", "devices.sort": "primary", "devices.order": "primary", "devices.pinned": "primary",
        "devices.activity_history": "primary", "devices.export": "primary", "devices.import": "primary", "devices.archive": "primary", "devices.all": "primary",
        "devices.add": "primary", "devices.install": "success",
        "devices.blocked": "danger", "devices.clear": "danger",
        "devices.refresh": "success", "devices.home": "primary",
    }
    assert all(by_id[page_id]["roles"] == ["owner"] for page_id in {
        "devices.saved_sets", "devices.collections", "devices.saved_filters", "devices.sort", "devices.order", "devices.pinned", "devices.activity_history", "devices.export", "devices.archive", "devices.all", "devices.add",
        "devices.install", "devices.blocked", "devices.clear",
    })


def test_device_tags_entry_and_editor_routes_are_owner_only():
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    device_entry = [row for row in registry["navigation"] if row["page_id"] == "device.tags"]
    editor = [row for row in registry["navigation"] if row["parent"] == "catalog.tags.list"]

    assert [(row["callback"], row["roles"], row["presentation"]) for row in device_entry] == [
        ("catalog:tags:list", ["owner"], "device_tags"),
    ]
    assert [(row["page_id"], row["callback"]) for row in editor] == [
        ("catalog.tags.edit", "catalog:tags:edit"),
        ("catalog.tags.back", "back:device"),
        ("catalog.tags.home", "menu:main"),
    ]
    assert all(row["roles"] == ["owner"] for row in editor)


def test_pinned_device_routes_are_owner_only_and_registry_backed():
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    toggle = [row for row in registry["navigation"] if row["page_id"] == "device.pinned_toggle"]
    pages = [row for row in registry["navigation"] if row["parent"] == "catalog.pinned.list"]

    assert [(row["callback"], row["roles"], row["presentation"]) for row in toggle] == [
        ("catalog:pinned:toggle:*", ["owner"], "device_pin_toggle"),
    ]
    assert [(row["page_id"], row["callback"], row["roles"]) for row in pages] == [
        ("catalog.pinned.row", "dev:*", ["owner"]),
        ("catalog.pinned.back", "menu:devices", ["owner"]),
        ("catalog.pinned.home", "menu:main", ["owner"]),
    ]


def test_device_comparison_routes_are_owner_only_and_declarative():
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    entry = [row for row in registry["navigation"] if row["page_id"] == "device.compare"]
    pages = [row for row in registry["navigation"] if row["parent"] == "catalog.device_compare"]
    result = [row for row in registry["navigation"] if row["parent"] == "catalog.device_compare.result"]

    assert [(row["callback"], row["roles"], row["presentation"]) for row in entry] == [
        ("catalog:compare:start:*", ["owner"], "device_compare_start"),
    ]
    assert [(row["page_id"], row["callback"], row["roles"]) for row in pages] == [
        ("catalog.device_compare.row", "catalog:compare:second:*", ["owner"]),
        ("catalog.device_compare.page", "catalog:compare:page:*", ["owner"]),
        ("catalog.device_compare.back", "menu:devices", ["owner"]),
    ]
    assert [(row["page_id"], row["callback"], row["roles"]) for row in result] == [
        ("catalog.device_compare.result.back", "menu:devices", ["owner"]),
        ("catalog.device_compare.result.home", "menu:main", ["owner"]),
    ]


def test_device_completeness_route_is_owner_only_and_registry_backed():
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    entry = [row for row in registry["navigation"] if row["page_id"] == "device.completeness"]
    result = [row for row in registry["navigation"] if row["parent"] == "catalog.device_completeness.result"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in entry] == [
        ("catalog:completeness:*", ["owner"], "device_completeness"),
    ]
    assert [(row["page_id"], row["callback"], row["roles"]) for row in result] == [
        ("catalog.device_completeness.back", "menu:devices", ["owner"]),
        ("catalog.device_completeness.home", "menu:main", ["owner"]),
    ]


def test_device_registration_origin_route_is_owner_only_and_registry_backed():
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    entry = [row for row in registry["navigation"] if row["page_id"] == "device.registration_origin"]
    result = [row for row in registry["navigation"] if row["parent"] == "catalog.device_registration.result"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in entry] == [
        ("catalog:registration:*", ["owner"], "device_registration_origin"),
    ]
    assert [(row["page_id"], row["callback"], row["roles"]) for row in result] == [
        ("catalog.device_registration.back", "menu:devices", ["owner"]),
        ("catalog.device_registration.home", "menu:main", ["owner"]),
    ]


def test_pinned_device_routes_are_owner_only_and_registry_backed():
    registry = build_feature_registry(ROOT / SPEC, "6d20370a06eb157428627f9002852514615e8ee2")
    toggle = [row for row in registry["navigation"] if row["page_id"] == "device.pinned_toggle"]
    pages = [row for row in registry["navigation"] if row["parent"] == "catalog.pinned.list"]

    assert [(row["callback"], row["roles"], row["presentation"]) for row in toggle] == [
        ("catalog:pinned:toggle:*", ["owner"], "device_pin_toggle"),
    ]
    assert [(row["page_id"], row["callback"], row["roles"]) for row in pages] == [
        ("catalog.pinned.row", "dev:*", ["owner"]),
        ("catalog.pinned.back", "menu:devices", ["owner"]),
        ("catalog.pinned.home", "menu:main", ["owner"]),
    ]


def test_valid_catalog_has_thirty_rows_per_area_and_does_not_load_runtime(tmp_path: Path) -> None:
    path = write_registry(tmp_path, valid_registry())

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "import validate_feature_registry as v; "
            "assert v.validate_feature_registry(Path(sys.argv[1])) == []; "
            "assert not {'bot', 'xgent_wds', 'xgent_mcs'} & sys.modules.keys()",
            str(path),
        ],
        cwd=ROOT / "tools",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_rejects_wrong_area_count_and_duplicate_feature_id(tmp_path: Path) -> None:
    data = valid_registry()
    data["features"].pop()
    data["features"][-1]["feature_id"] = data["features"][0]["feature_id"]

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert has_error(errors, "AREA_FEATURE_COUNT")
    assert has_error(errors, "DUPLICATE_ID")


def test_rejects_missing_id_invalid_state_and_unknown_fields(tmp_path: Path) -> None:
    data = valid_registry()
    feature = data["features"][0]
    del feature["feature_id"]
    feature["support_state"] = "maybe"
    feature["exec"] = "run arbitrary callback"

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert has_error(errors, "MISSING_FIELD")
    assert has_error(errors, "INVALID_STATE")
    assert has_error(errors, "UNKNOWN_FIELD")


def test_rejects_missing_feature_description(tmp_path: Path) -> None:
    data = valid_registry()
    del data["features"][0]["description"]

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert has_error(errors, "MISSING_FIELD")


def test_rejects_secret_like_values_anywhere_in_registry(tmp_path: Path) -> None:
    data = valid_registry()
    data["features"][0]["outcome"] = "ghp_" + "A" * 36

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert has_error(errors, "SECRET_LIKE")


def test_rejects_unresolved_evidence_and_test_references(tmp_path: Path) -> None:
    data = valid_registry()
    feature = data["features"][0]
    feature["baseline_evidence"] = [
        {"path": "missing-baseline.md", "reference": "absent", "novelty_status": "confirmed_absent"}
    ]
    feature["test_refs"] = ["missing_test.py::test_missing"]
    feature["support_state"] = "implemented"
    feature["handler_id"] = "feature.b01.f01"

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert has_error(errors, "UNRESOLVED_EVIDENCE")
    assert has_error(errors, "UNRESOLVED_TEST")


def test_rejects_malformed_json(tmp_path: Path) -> None:
    errors = validate_feature_registry(write_registry(tmp_path, "{not-json"))

    assert has_error(errors, "JSON_PARSE")


def test_rejects_duplicate_or_unresolved_navigation_page_metadata(tmp_path: Path) -> None:
    data = valid_registry()
    data["navigation"][1]["page_id"] = data["navigation"][0]["page_id"]
    data["navigation"][1]["callback"] = data["navigation"][0]["callback"]
    data["navigation"][2]["roles"] = ["robot"]

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert has_error(errors, "DUPLICATE_NAVIGATION")
    assert has_error(errors, "INVALID_NAVIGATION")


def test_allows_same_static_callback_under_distinct_navigation_parents(tmp_path: Path) -> None:
    data = valid_registry()
    second_page = dict(data["navigation"][0])
    second_page["page_id"] = "screen.status"
    second_page["parent"] = "screen"
    data["navigation"].append(second_page)

    errors = validate_feature_registry(write_registry(tmp_path, data))

    assert not has_error(errors, "DUPLICATE_NAVIGATION")


def test_builder_migrates_only_the_approved_unique_catalog_rows(tmp_path: Path) -> None:
    spec = importlib.util.find_spec("build_feature_registry")
    assert spec is not None
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)

    registry = builder.build_feature_registry(ROOT / SPEC)

    expected_ids = {
        f"{area}-F{number:02d}"
        for area in AREA_IDS
        for number in range(1, 31)
    }
    features = registry["features"]
    assert registry["baseline"]["commit"] == "6d20370a06eb157428627f9002852514615e8ee2"
    assert len(registry["areas"]) == 17
    assert {feature["feature_id"] for feature in features} == expected_ids
    assert len(features) == 510
    assert all(isinstance(feature.get("description"), str) and feature["description"].strip() for feature in features)
    p01_f25 = next(feature for feature in features if feature["feature_id"] == "P01-F25")
    assert p01_f25["support_state"] == "needs_device_test"
    assert p01_f25["handler_id"] == "p01.f25.location.compare"
    assert set(p01_f25["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_location.py::test_comparison_keyboard_and_callback_are_owner_only",
        "XGENT-WDS/tests/test_location.py::test_owner_compare_opt_in_returns_distinct_system_and_ip_sources_without_persisting",
        "XGENT-MCS/tests/test_location.py::test_owner_compare_opt_in_returns_distinct_system_and_ip_sources_without_persisting",
    }
    p01_f09 = next(feature for feature in features if feature["feature_id"] == "P01-F09")
    assert p01_f09["support_state"] == "needs_device_test"
    assert p01_f09["handler_id"] == "p01.f09.permission.recheck"
    assert p01_f09["test_refs"] == [
        "TG-BOT-SERVER/tests/test_capabilities.py::test_permission_settings_success_offers_one_shot_fresh_check_bound_to_permission",
    ]
    d01_f23 = next(feature for feature in features if feature["feature_id"] == "D01-F23")
    assert d01_f23["support_state"] == "needs_device_test"
    assert d01_f23["handler_id"] == "d01.f23.regional.snapshot"
    assert set(d01_f23["test_refs"]) == {
        "XGENT-WDS/tests/test_capabilities.py::test_system_region_snapshot_has_separate_truthful_timezone_and_locale_sources",
        "XGENT-WDS/tests/test_capabilities.py::test_sysinfo_embeds_the_correlated_regional_snapshot",
        "XGENT-MCS/tests/test_capabilities.py::test_system_region_snapshot_has_separate_truthful_timezone_and_locale_sources",
        "XGENT-MCS/tests/test_capabilities.py::test_sysinfo_embeds_the_correlated_regional_snapshot",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_renders_only_reported_timezone_and_locale_as_escaped_values",
    }
    d01_f01 = next(feature for feature in features if feature["feature_id"] == "D01-F01")
    assert d01_f01["support_state"] == "needs_device_test"
    assert d01_f01["handler_id"] == "d01.f01.health.overview"
    assert set(d01_f01["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_shows_explainable_resource_health_instead_of_an_opaque_score",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_marks_missing_or_invalid_health_values_unknown",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_does_not_call_health_values_fresh_without_request_correlation",
    }
    d01_f02 = next(feature for feature in features if feature["feature_id"] == "D01-F02")
    assert d01_f02["support_state"] == "needs_device_test"
    assert d01_f02["handler_id"] == "d01.f02.cpu.history"
    assert set(d01_f02["test_refs"]) == {
        "XGENT-WDS/tests/test_capabilities.py::test_cpu_history_returns_bounded_correlated_samples_for_selected_period",
        "XGENT-WDS/tests/test_capabilities.py::test_cpu_history_rejects_unbounded_period_without_sampling",
        "XGENT-MCS/tests/test_capabilities.py::test_cpu_history_returns_bounded_correlated_samples_for_selected_period",
        "XGENT-MCS/tests/test_capabilities.py::test_cpu_history_rejects_unbounded_period_without_sampling",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_cpu_history_renders_only_a_correlated_selected_period",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_cpu_history_hides_samples_for_stale_or_malformed_results",
    }
    d01_f03 = next(feature for feature in features if feature["feature_id"] == "D01-F03")
    assert d01_f03["support_state"] == "needs_device_test"
    assert d01_f03["handler_id"] == "d01.f03.memory.history"
    assert set(d01_f03["test_refs"]) == {
        "XGENT-WDS/tests/test_capabilities.py::test_memory_history_returns_bounded_correlated_ram_and_swap_samples",
        "XGENT-WDS/tests/test_capabilities.py::test_memory_history_marks_absent_swap_unsupported_not_zero",
        "XGENT-MCS/tests/test_capabilities.py::test_memory_history_returns_bounded_correlated_ram_and_swap_samples",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_history_renders_only_a_correlated_bounded_ram_and_swap_series",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_history_does_not_invent_swap_zero_or_render_stale_samples",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_history_keeps_ram_when_swap_series_is_partial_and_marks_swap_unknown",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_history_period_picker_is_owner_only_and_registry_backed",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_history_does_not_wait_for_agent_that_advertises_no_command",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_history_updates_the_existing_card_with_its_correlated_result",
    }
    d01_f04 = next(feature for feature in features if feature["feature_id"] == "D01-F04")
    assert d01_f04["support_state"] == "needs_device_test"
    assert d01_f04["handler_id"] == "d01.f04.volume.history"
    assert set(d01_f04["test_refs"]) == {
        "XGENT-WDS/tests/test_capabilities.py::test_volume_history_keeps_real_samples_and_withholds_short_term_forecast",
        "XGENT-WDS/tests/test_capabilities.py::test_volume_history_estimates_only_from_same_volume_capacity_after_six_hours",
        "XGENT-MCS/tests/test_capabilities.py::test_volume_history_keeps_real_samples_and_withholds_short_term_forecast",
        "XGENT-MCS/tests/test_capabilities.py::test_volume_history_estimates_only_from_same_volume_capacity_after_six_hours",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_volume_history_renders_correlated_live_usage_and_measured_forecast",
        "TG-BOT-SERVER/tests/test_capabilities.py::test_volume_history_hides_stale_or_unreliable_forecast_values",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_volume_history_is_owner_only_and_registry_backed",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_volume_history_updates_the_existing_card_with_correlated_result",
    }
    d01_f05 = next(feature for feature in features if feature["feature_id"] == "D01-F05")
    assert d01_f05["support_state"] == "needs_device_test"
    assert d01_f05["handler_id"] == "d01.f05.memory.pressure.history"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_pressure_renderer_preserves_platform_source_and_metric_meaning" in d01_f05["test_refs"]
    d01_f06 = next(feature for feature in features if feature["feature_id"] == "D01-F06")
    d01_f07 = next(feature for feature in features if feature["feature_id"] == "D01-F07")
    d01_f08 = next(feature for feature in features if feature["feature_id"] == "D01-F08")
    d01_f09 = next(feature for feature in features if feature["feature_id"] == "D01-F09")
    assert d01_f06["support_state"] == "needs_device_test"
    assert d01_f06["handler_id"] == "d01.f06.thermal.snapshot"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_thermal_snapshot_renderer_distinguishes_sensor_data_and_unsupported_platform" in d01_f06["test_refs"]
    assert d01_f07["support_state"] == "needs_device_test"
    assert d01_f07["handler_id"] == "d01.f07.battery.health"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_battery_health_renderer_separates_wear_from_charge_and_rejects_bad_data" in d01_f07["test_refs"]
    assert d01_f08["support_state"] == "needs_device_test"
    assert d01_f08["handler_id"] == "d01.f08.battery.cycles"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_battery_cycle_renderer_reports_only_correlated_provider_counts" in d01_f08["test_refs"]
    cycles_page = next(page for page in registry["navigation"] if page["page_id"] == "system.battery_cycles")
    assert cycles_page["callback"] == "battery:cycles"
    assert cycles_page["presentation"] == "battery_cycles"
    assert d01_f09["support_state"] == "needs_device_test"
    assert d01_f09["handler_id"] == "d01.f09.energy.usage"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_energy_usage_renderer_shows_only_correlated_estimates_and_redacts_paths" in d01_f09["test_refs"]
    d01_f10 = next(feature for feature in features if feature["feature_id"] == "D01-F10")
    assert d01_f10["support_state"] == "needs_device_test"
    assert d01_f10["handler_id"] == "d01.f10.sleep.wake.history"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_sleep_wake_renderer_accepts_only_correlated_recent_allowlisted_events" in d01_f10["test_refs"]
    sleep_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.sleep_wake_history")
    assert sleep_history_page["callback"] == "sleep:history"
    assert sleep_history_page["presentation"] == "sleep_wake_history"
    d01_f11 = next(feature for feature in features if feature["feature_id"] == "D01-F11")
    assert d01_f11["support_state"] == "needs_device_test"
    assert d01_f11["handler_id"] == "d01.f11.startup.duration"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_startup_duration_renderer_uses_measured_boot_duration_not_uptime" in d01_f11["test_refs"]
    startup_duration_page = next(page for page in registry["navigation"] if page["page_id"] == "system.startup_duration")
    assert startup_duration_page["callback"] == "boot:duration"
    assert startup_duration_page["presentation"] == "startup_duration"
    pending_restart = next(feature for feature in features if feature["feature_id"] == "D01-F12")
    assert pending_restart["support_state"] == "needs_device_test"
    assert pending_restart["handler_id"] == "d01.f12.pending.restart"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_pending_restart_renderer_requires_fresh_correlation_and_fixed_reason_ids" in pending_restart["test_refs"]
    pending_restart_page = next(page for page in registry["navigation"] if page["page_id"] == "system.pending_restart")
    assert pending_restart_page["callback"] == "reboot:status"
    assert pending_restart_page["presentation"] == "pending_restart"
    d01_f13 = next(feature for feature in features if feature["feature_id"] == "D01-F13")
    assert d01_f13["support_state"] == "needs_device_test"
    assert d01_f13["handler_id"] == "d01.f13.os.support"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_os_support_renderer_is_dated_source_labelled_and_correlation_bound" in d01_f13["test_refs"]
    os_support_page = next(page for page in registry["navigation"] if page["page_id"] == "system.os_support")
    assert os_support_page["callback"] == "os:support"
    assert os_support_page["presentation"] == "os_support"
    hardware_history = next(feature for feature in features if feature["feature_id"] == "D01-F14")
    assert hardware_history["support_state"] == "needs_device_test"
    assert hardware_history["handler_id"] == "d01.f14.hardware.inventory.history"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_hardware_inventory_history_is_correlated_owner_only_and_redacted" in hardware_history["test_refs"]
    hardware_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.hardware_inventory")
    assert hardware_history_page["callback"] == "hardware:history"
    assert hardware_history_page["presentation"] == "hardware_inventory_history"
    driver_history = next(feature for feature in features if feature["feature_id"] == "D01-F15")
    assert driver_history["support_state"] == "needs_device_test"
    assert driver_history["handler_id"] == "d01.f15.driver.inventory.history"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_driver_inventory_history_is_correlated_owner_only_and_redacted" in driver_history["test_refs"]
    driver_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.driver_inventory")
    assert driver_history_page["callback"] == "drivers:history"
    assert driver_history_page["presentation"] == "driver_inventory_history"
    installed_app_history = next(feature for feature in features if feature["feature_id"] == "D01-F16")
    assert installed_app_history["support_state"] == "needs_device_test"
    assert installed_app_history["handler_id"] == "d01.f16.installed.app.history"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_installed_app_history_is_correlated_owner_only_and_redacted" in installed_app_history["test_refs"]
    installed_app_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.installed_app_inventory")
    assert installed_app_history_page["callback"] == "apps:history"
    assert installed_app_history_page["presentation"] == "installed_app_history"
    startup_history = next(feature for feature in features if feature["feature_id"] == "D01-F17")
    assert startup_history["support_state"] == "needs_device_test"
    assert startup_history["handler_id"] == "d01.f17.startup.history"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_startup_inventory_history_is_correlated_owner_only_and_redacted" in startup_history["test_refs"]
    startup_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.startup_inventory")
    assert startup_history_page["callback"] == "startup:history"
    assert startup_history_page["presentation"] == "startup_inventory_history"
    service_history = next(feature for feature in features if feature["feature_id"] == "D01-F18")
    assert service_history["support_state"] == "needs_device_test"
    assert service_history["handler_id"] == "d01.f18.service.history"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_service_inventory_history_is_correlated_owner_only_and_redacted" in service_history["test_refs"]
    service_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.service_inventory")
    assert service_history_page["callback"] == "services:history"
    assert service_history_page["presentation"] == "service_inventory_history"
    crash_summary = next(feature for feature in features if feature["feature_id"] == "D01-F19")
    assert crash_summary["support_state"] == "needs_device_test"
    assert crash_summary["handler_id"] == "d01.f19.crash.summary"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_crash_summary_is_correlated_owner_only_and_redacted" in crash_summary["test_refs"]
    crash_summary_page = next(page for page in registry["navigation"] if page["page_id"] == "system.crash_summary")
    assert crash_summary_page["callback"] == "crash:summary"
    assert crash_summary_page["presentation"] == "crash_summary"
    smart_trend = next(feature for feature in features if feature["feature_id"] == "D01-F20")
    assert smart_trend["support_state"] == "needs_device_test"
    assert smart_trend["handler_id"] == "d01.f20.smart.counter.trend"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_smart_trend_is_owner_only_correlated_and_persists_two_device_snapshots" in smart_trend["test_refs"]
    smart_trend_page = next(page for page in registry["navigation"] if page["page_id"] == "system.smart_trend")
    assert smart_trend_page["callback"] == "smart:trend"
    assert smart_trend_page["roles"] == ["owner"]
    assert smart_trend_page["presentation"] == "smart_trend"
    volume_topology = next(feature for feature in features if feature["feature_id"] == "D01-F21")
    assert volume_topology["support_state"] == "needs_device_test"
    assert volume_topology["handler_id"] == "d01.f21.volume.topology"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_volume_topology_is_owner_only_correlated_and_updates_one_card" in volume_topology["test_refs"]
    volume_topology_page = next(page for page in registry["navigation"] if page["page_id"] == "system.volume_topology")
    assert volume_topology_page["callback"] == "volume:topology"
    assert volume_topology_page["roles"] == ["owner"]
    assert volume_topology_page["presentation"] == "volume_topology"
    dns_resolution = next(feature for feature in features if feature["feature_id"] == "D04-F05")
    assert dns_resolution["support_state"] == "needs_device_test"
    assert dns_resolution["handler_id"] == "d04.f05.network.dns_resolution"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_dns_resolution_is_owner_only_correlated_and_edits_one_card" in dns_resolution["test_refs"]
    dns_page = next(page for page in registry["navigation"] if page["page_id"] == "network.dns_resolution")
    assert dns_page["callback"] == "cmd:dns_resolution"
    assert dns_page["roles"] == ["owner"]
    assert dns_page["presentation"] == "network_dns_resolution"
    dns_settings = next(feature for feature in features if feature["feature_id"] == "D04-F06")
    assert dns_settings["support_state"] == "needs_device_test"
    assert dns_settings["handler_id"] == "d04.f06.network.dns_settings"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_dns_settings_is_owner_only_correlated_and_edits_one_card" in dns_settings["test_refs"]
    dns_settings_page = next(page for page in registry["navigation"] if page["page_id"] == "network.dns_settings")
    assert dns_settings_page["callback"] == "cmd:dns_settings"
    assert dns_settings_page["roles"] == ["owner"]
    assert dns_settings_page["presentation"] == "network_dns_settings"
    dns_comparison = next(feature for feature in features if feature["feature_id"] == "D04-F07")
    assert dns_comparison["support_state"] == "needs_device_test"
    assert dns_comparison["handler_id"] == "d04.f07.network.dns_comparison"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_dns_comparison_is_owner_only_correlated_and_edits_one_card" in dns_comparison["test_refs"]
    dns_comparison_page = next(page for page in registry["navigation"] if page["page_id"] == "network.dns_comparison")
    assert dns_comparison_page["callback"] == "cmd:dns_comparison"
    assert dns_comparison_page["roles"] == ["owner"]
    assert dns_comparison_page["presentation"] == "network_dns_comparison"
    clock_drift = next(feature for feature in features if feature["feature_id"] == "D01-F22")
    assert clock_drift["support_state"] == "needs_device_test"
    assert clock_drift["handler_id"] == "d01.f22.clock.drift.local"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_clock_drift_is_owner_only_correlated_and_explains_local_estimate" in clock_drift["test_refs"]
    clock_drift_page = next(page for page in registry["navigation"] if page["page_id"] == "system.clock_drift")
    assert clock_drift_page["callback"] == "clock:drift"
    assert clock_drift_page["roles"] == ["owner"]
    assert clock_drift_page["presentation"] == "clock_drift"
    summary_history = next(feature for feature in features if feature["feature_id"] == "D01-F24")
    assert summary_history["support_state"] == "needs_device_test"
    assert summary_history["handler_id"] == "d01.f24.system.summary.history"
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_system_summary_history_is_allowlisted_bounded_and_expires" in summary_history["test_refs"]
    summary_history_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_history")
    assert summary_history_page["callback"] == "sysinfo:history"
    assert summary_history_page["roles"] == ["owner"]
    assert summary_history_page["presentation"] == "system_summary_history"
    summary_comparison = next(feature for feature in features if feature["feature_id"] == "D01-F25")
    assert summary_comparison["support_state"] == "needs_device_test"
    assert summary_comparison["handler_id"] == "d01.f25.system.summary.compare"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_snapshot_comparison_is_owner_only_and_edits_one_card" in summary_comparison["test_refs"]
    summary_comparison_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_comparison")
    assert summary_comparison_page["callback"] == "sysinfo:compare"
    assert summary_comparison_page["roles"] == ["owner"]
    assert summary_comparison_page["presentation"] == "system_summary_comparison"
    summary_export = next(feature for feature in features if feature["feature_id"] == "D01-F29")
    assert summary_export["support_state"] == "needs_device_test"
    assert summary_export["handler_id"] == "d01.f29.system.summary.export"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_snapshot_export_selects_allowlisted_snapshot_and_keeps_one_card" in summary_export["test_refs"]
    summary_export_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_export")
    assert summary_export_page["callback"] == "sysinfo:export"
    assert summary_export_page["roles"] == ["owner"]
    assert summary_export_page["presentation"] == "system_summary_export"
    summary_interval = next(feature for feature in features if feature["feature_id"] == "D01-F26")
    assert summary_interval["support_state"] == "needs_device_test"
    assert summary_interval["handler_id"] == "d01.f26.system.summary.interval"
    assert "XGENT-WDS/tests/test_sysinfo_schedule.py::test_scheduler_publishes_only_sanitized_metrics_and_coalesces_missed_ticks" in summary_interval["test_refs"]
    summary_interval_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_interval")
    assert summary_interval_page["callback"] == "sysinfo:interval"
    assert summary_interval_page["roles"] == ["owner"]
    assert summary_interval_page["presentation"] == "system_summary_interval"
    summary_thresholds = next(feature for feature in features if feature["feature_id"] == "D01-F27")
    assert summary_thresholds["support_state"] == "needs_device_test"
    assert summary_thresholds["handler_id"] == "d01.f27.system.summary.thresholds"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_scheduled_threshold_notice_requires_two_samples_and_valid_sensor_provenance" in summary_thresholds["test_refs"]
    summary_thresholds_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_thresholds")
    assert summary_thresholds_page["callback"] == "sysinfo:threshold"
    assert summary_thresholds_page["roles"] == ["owner"]
    assert summary_thresholds_page["presentation"] == "system_summary_thresholds"
    summary_aggregate = next(feature for feature in features if feature["feature_id"] == "D01-F28")
    assert summary_aggregate["support_state"] == "needs_device_test"
    assert summary_aggregate["handler_id"] == "d01.f28.system.summary.aggregate"
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_scheduled_system_summary_aggregates_only_fresh_scheduled_samples_by_period" in summary_aggregate["test_refs"]
    summary_aggregate_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_aggregate")
    assert summary_aggregate_page["callback"] == "sysinfo:aggregate"
    assert summary_aggregate_page["roles"] == ["owner"]
    assert summary_aggregate_page["presentation"] == "system_summary_aggregate"
    summary_uncertainty = next(feature for feature in features if feature["feature_id"] == "D01-F30")
    assert summary_uncertainty["support_state"] == "needs_device_test"
    assert summary_uncertainty["handler_id"] == "d01.f30.system.summary.uncertainty"
    assert "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_uncertainty_distinguishes_measured_estimated_missing_and_stale" in summary_uncertainty["test_refs"]
    summary_uncertainty_page = next(page for page in registry["navigation"] if page["page_id"] == "system.summary_uncertainty")
    assert summary_uncertainty_page["callback"] == "sysinfo:uncertainty"
    assert summary_uncertainty_page["roles"] == ["owner"]
    assert summary_uncertainty_page["presentation"] == "system_summary_uncertainty"
    camera_quality = next(feature for feature in features if feature["feature_id"] == "D02-F13")
    assert camera_quality["support_state"] == "needs_device_test"
    assert camera_quality["handler_id"] == "d02.f13.camera.quality.local"
    assert "XGENT-WDS/tests/test_camera_quality.py::test_camera_quality_captures_one_local_frame_and_never_returns_image" in camera_quality["test_refs"]
    camera_quality_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.camera_quality")
    assert camera_quality_page["callback"] == "camera:quality"
    assert camera_quality_page["roles"] == ["owner"]
    assert camera_quality_page["presentation"] == "camera_quality"
    screenshot_mask = next(feature for feature in features if feature["feature_id"] == "D02-F04")
    assert screenshot_mask["support_state"] == "needs_device_test"
    assert screenshot_mask["handler_id"] == "d02.f04.screenshot.mask.preview"
    assert "TG-BOT-SERVER/tests/test_screenshot.py::test_owner_mask_flow_keeps_raw_image_out_of_telegram_and_previews_only_masked_copy" in screenshot_mask["test_refs"]
    screenshot_mask_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.screenshot.mask")
    assert screenshot_mask_page["callback"] == "screenshot:mask"
    assert screenshot_mask_page["roles"] == ["owner"]
    screenshot_preview = next(feature for feature in features if feature["feature_id"] == "D02-F05")
    assert screenshot_preview["support_state"] == "needs_device_test"
    assert screenshot_preview["handler_id"] == "d02.f05.screenshot.preview"
    screenshot_preview_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.screenshot.preview")
    assert (screenshot_preview_page["callback"], screenshot_preview_page["roles"], screenshot_preview_page["presentation"]) == (
        "screenshot:preview", ["owner"], "screen_action",
    )
    media_temp = next(feature for feature in features if feature["feature_id"] == "D02-F23")
    assert media_temp["support_state"] == "needs_device_test"
    assert media_temp["handler_id"] == "d02.f23.media_temp.cleanup"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_media_temp_cleanup_is_owner_only_and_rejects_uncorrelated_reports" in media_temp["test_refs"]
    media_temp_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.media_temp.cleanup")
    assert media_temp_page["callback"] == "media:temp:cleanup"
    assert media_temp_page["roles"] == ["owner"]
    display_sheet = next(feature for feature in features if feature["feature_id"] == "D02-F06")
    assert display_sheet["support_state"] == "needs_device_test"
    assert display_sheet["handler_id"] == "d02.f06.display.contact_sheet"
    display_sheet_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.display.contact_sheet")
    assert display_sheet_page["callback"] == "display:catalog"
    assert display_sheet_page["roles"] == ["owner"]
    annotation_copy = next(feature for feature in features if feature["feature_id"] == "D02-F07")
    assert annotation_copy["support_state"] == "needs_device_test"
    assert annotation_copy["handler_id"] == "d02.f07.screenshot.annotation_copy"
    assert annotation_copy["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_annotation_updates_the_same_card_and_preserves_original" in annotation_copy["test_refs"]
    screenshot_compare = next(feature for feature in features if feature["feature_id"] == "D02-F08")
    assert screenshot_compare["support_state"] == "needs_device_test"
    assert screenshot_compare["handler_id"] == "d02.f08.screenshot.visual_compare"
    assert screenshot_compare["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_comparison_captures_second_image_and_sends_only_confirmed_diff" in screenshot_compare["test_refs"]
    display_single = next(feature for feature in features if feature["feature_id"] == "D02-F01")
    assert display_single["support_state"] == "needs_device_test"
    assert display_single["handler_id"] == "d02.f01.display.screenshot"
    display_single_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.display.single")
    assert display_single_page["callback"] == "display:screenshot"
    assert display_single_page["roles"] == ["owner"]
    display_region = next(feature for feature in features if feature["feature_id"] == "D02-F03")
    assert display_region["support_state"] == "needs_device_test"
    assert display_region["handler_id"] == "d02.f03.display.region.screenshot"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_region_result_requires_exact_request_and_cropped_dimensions" in display_region["test_refs"]
    exact_search = next(feature for feature in features if feature["feature_id"] == "B01-F01")
    assert exact_search["support_state"] == "needs_device_test"
    assert exact_search["handler_id"] == "b01.f01.catalog.search.exact"
    assert "TG-BOT-SERVER/tests/test_feature_catalog.py::test_exact_and_outcome_search_are_distinct" in exact_search["test_refs"]
    outcome_search = next(feature for feature in features if feature["feature_id"] == "B01-F18")
    assert outcome_search["support_state"] == "needs_device_test"
    assert outcome_search["handler_id"] == "b01.f18.catalog.search.outcome"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_search_is_available_in_bot_and_edits_one_card" in outcome_search["test_refs"]
    fuzzy_search = next(feature for feature in features if feature["feature_id"] == "B01-F02")
    assert fuzzy_search["support_state"] == "needs_device_test"
    assert fuzzy_search["handler_id"] == "b01.f02.catalog.search.fuzzy"
    assert "TG-BOT-SERVER/tests/test_feature_catalog.py::test_fuzzy_search_corrects_typos_only_in_identity_and_ranks_closest_title" in fuzzy_search["test_refs"]
    result_filter = next(feature for feature in features if feature["feature_id"] == "B01-F05")
    assert result_filter["support_state"] == "needs_device_test"
    assert result_filter["handler_id"] == "b01.f05.catalog.filter.result_type"
    assert result_filter["result_types"] == ["text"]
    permission_filter = next(feature for feature in features if feature["feature_id"] == "B01-F06")
    assert permission_filter["support_state"] == "needs_device_test"
    assert permission_filter["handler_id"] == "b01.f06.catalog.filter.permission"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_permission_filter_uses_fresh_device_snapshot_and_preserves_rbac" in permission_filter["test_refs"]
    access_filter = next(feature for feature in features if feature["feature_id"] == "B01-F07")
    assert access_filter["support_state"] == "needs_device_test"
    assert access_filter["handler_id"] == "b01.f07.catalog.filter.access"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_access_filter_uses_current_grants_and_selected_device" in access_filter["test_refs"]
    availability_explainer = next(feature for feature in features if feature["feature_id"] == "B01-F08")
    assert availability_explainer["support_state"] == "needs_device_test"
    assert availability_explainer["handler_id"] == "b01.f08.catalog.explain_availability"
    assert "TG-BOT-SERVER/tests/test_feature_catalog.py::test_availability_explainer_reports_independent_role_grant_os_and_permission_blockers" in availability_explainer["test_refs"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_feature_detail_explains_selected_device_os_and_permission_blockers" in availability_explainer["test_refs"]
    platform_filter = next(feature for feature in features if feature["feature_id"] == "B01-F04")
    assert platform_filter["support_state"] == "needs_device_test"
    assert platform_filter["handler_id"] == "b01.f04.catalog.filter.platform"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_area_page_filters_to_selected_device_platform" in platform_filter["test_refs"]
    synonyms = next(feature for feature in features if feature["feature_id"] == "B01-F03")
    assert synonyms["support_state"] == "needs_device_test"
    assert synonyms["handler_id"] == "b01.f03.catalog.search.synonyms"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_catalog_synonym_flow_edits_one_card_and_only_changes_exact_search" in synonyms["test_refs"]
    display_resolution = next(feature for feature in features if feature["feature_id"] == "D02-F10")
    assert display_resolution["support_state"] == "needs_device_test"
    assert display_resolution["handler_id"] == "d02.f10.display.resolution"
    display_resolution_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.display.resolution")
    assert display_resolution_page["callback"] == "display:resolution"
    assert display_resolution_page["roles"] == ["owner"]
    window_capture = next(feature for feature in features if feature["feature_id"] == "D02-F02")
    assert window_capture["support_state"] == "needs_device_test"
    assert window_capture["handler_id"] == "d02.f02.window.screenshot"
    window_capture_page = next(page for page in registry["navigation"] if page["page_id"] == "screen.window.single")
    assert window_capture_page["callback"] == "window:screenshot"
    assert window_capture_page["roles"] == ["owner"]
    a01_f01 = next(feature for feature in features if feature["feature_id"] == "A01-F01")
    assert a01_f01["support_state"] == "needs_device_test"
    assert a01_f01["handler_id"] == "a01.f01.admin.temporary_grant"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_temporary_admin_grant_requires_owner_confirmation_and_expires" in a01_f01["test_refs"]
    a01_f05 = next(feature for feature in features if feature["feature_id"] == "A01-F05")
    assert a01_f05["support_state"] == "needs_device_test"
    assert a01_f05["handler_id"] == "a01.f05.rbac.denial_reason"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_access_explanation_reports_nonsecret_rbac_denial_without_changing_policy" in a01_f05["test_refs"]
    a01_f06 = next(feature for feature in features if feature["feature_id"] == "A01-F06")
    assert a01_f06["support_state"] == "needs_device_test"
    assert a01_f06["handler_id"] == "a01.f06.admin.grant_review"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_grant_review_distinguishes_expired_unused_and_unknown_history" in a01_f06["test_refs"]
    a01_f07 = next(feature for feature in features if feature["feature_id"] == "A01-F07")
    assert a01_f07["support_state"] == "needs_device_test"
    assert a01_f07["handler_id"] == "a01.f07.admin.grant_revocation_preview"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_grant_revocation_preview_lists_exact_affected_scope_without_mutation" in a01_f07["test_refs"]
    a01_f08 = next(feature for feature in features if feature["feature_id"] == "A01-F08")
    assert a01_f08["support_state"] == "needs_device_test"
    assert a01_f08["handler_id"] == "a01.f08.admin.temporary_block"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_temporary_user_block_requires_reason_preview_and_one_use_confirmation" in a01_f08["test_refs"]
    a01_f09 = next(feature for feature in features if feature["feature_id"] == "A01-F09")
    assert a01_f09["support_state"] == "needs_device_test"
    assert a01_f09["handler_id"] == "a01.f09.admin.block_impact_preview"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_block_confirmation_shows_the_access_it_will_revoke" in a01_f09["test_refs"]
    a01_f10 = next(feature for feature in features if feature["feature_id"] == "A01-F10")
    assert a01_f10["support_state"] == "needs_device_test"
    assert a01_f10["handler_id"] == "a01.f10.access.expiry_reminder"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_expiring_grant_delivery_failure_releases_claim_for_retry" in a01_f10["test_refs"]
    a01_f11 = next(feature for feature in features if feature["feature_id"] == "A01-F11")
    assert a01_f11["support_state"] == "needs_device_test"
    assert a01_f11["handler_id"] == "a01.f11.admin.user_role_history"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_owner_can_review_exact_role_history_for_one_user" in a01_f11["test_refs"]
    a01_f12 = next(feature for feature in features if feature["feature_id"] == "A01-F12")
    assert a01_f12["support_state"] == "needs_device_test"
    assert a01_f12["handler_id"] == "a01.f12.admin.user_grant_history"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_owner_grant_history_is_typed_target_scoped_and_includes_expiry" in a01_f12["test_refs"]
    a01_f24 = next(feature for feature in features if feature["feature_id"] == "A01-F24")
    assert a01_f24["support_state"] == "needs_device_test"
    assert a01_f24["handler_id"] == "a01.f24.admin.user_revoke_all_grants"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_owner_revokes_all_target_grants_only_after_exact_snapshot_confirmation" in a01_f24["test_refs"]
    revoke_all_route = next(page for page in registry["navigation"] if page["page_id"] == "admin.user.revoke_all_grants")
    assert revoke_all_route["callback"] == "admin:revoke-user:*"
    assert revoke_all_route["roles"] == ["owner"]
    assert revoke_all_route["presentation"] == "admin_user_revoke_all_grants"
    a01_f13 = next(feature for feature in features if feature["feature_id"] == "A01-F13")
    assert a01_f13["support_state"] == "needs_device_test"
    assert a01_f13["handler_id"] == "a01.f13.admin.audit.filter"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_audit_filter_text_edits_one_owner_card_with_filtered_rows" in a01_f13["test_refs"]
    a01_f14 = next(feature for feature in features if feature["feature_id"] == "A01-F14")
    assert a01_f14["support_state"] == "needs_device_test"
    assert a01_f14["handler_id"] == "a01.f14.admin.audit.event_detail"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_audit_event_opens_only_normalized_owner_projection" in a01_f14["test_refs"]
    a01_f15 = next(feature for feature in features if feature["feature_id"] == "A01-F15")
    assert a01_f15["support_state"] == "needs_device_test"
    assert a01_f15["handler_id"] == "a01.f15.admin.audit.export"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_owner_audit_export_is_range_limited_redacted_and_capped" in a01_f15["test_refs"]
    a01_f16 = next(feature for feature in features if feature["feature_id"] == "A01-F16")
    assert a01_f16["support_state"] == "needs_device_test"
    assert a01_f16["handler_id"] == "a01.f16.admin.permission.conflict_preview"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_grant_confirmation_previews_overlapping_access_before_owner_decision" in a01_f16["test_refs"]
    a01_f17 = next(feature for feature in features if feature["feature_id"] == "A01-F17")
    assert a01_f17["support_state"] == "needs_device_test"
    assert a01_f17["handler_id"] == "a01.f17.admin.device_grants.stale_scan"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_stale_device_grants_screen_shows_only_missing_registry_ids" in a01_f17["test_refs"]
    stale_grants_routes = [row for row in registry["navigation"] if row["parent"] == "admin.stale_grants"]
    assert {(row["page_id"], row["callback"], row["label_id"], row["presentation"]) for row in stale_grants_routes} == {
        ("admin.stale_grants.refresh", "admin:stale-grants:refresh", "stale_grants_refresh", "stale_grants_refresh"),
        ("admin.stale_grants.back", "menu:admin", "back", "nav"),
    }
    a01_f18 = next(feature for feature in features if feature["feature_id"] == "A01-F18")
    assert a01_f18["support_state"] == "needs_device_test"
    assert a01_f18["handler_id"] == "a01.f18.admin.monthly_access_review"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_monthly_access_review_is_owner_only_paginated_and_read_only" in a01_f18["test_refs"]
    access_review_routes = [row for row in registry["navigation"] if row["parent"] == "admin.access_review"]
    assert {(row["page_id"], row["callback"], row["label_id"], row["presentation"]) for row in access_review_routes} == {
        ("admin.access_review.refresh", "admin:access-review", "access_review_refresh", "access_review_refresh"),
        ("admin.access_review.page", "admin:access-review:page:*", "access_review_page", "access_review_page"),
        ("admin.access_review.back", "menu:admin", "back", "nav"),
    }
    a01_f19 = next(feature for feature in features if feature["feature_id"] == "A01-F19")
    assert a01_f19["support_state"] == "needs_device_test"
    assert a01_f19["handler_id"] == "a01.f19.admin.bulk_grant.preview"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_grant_confirmation_applies_once_and_reports_exact_counts" in a01_f19["test_refs"]
    bulk_grant_routes = [row for row in registry["navigation"] if row["page_id"] == "admin.bulk_grant"]
    assert [(row["callback"], row["label_id"], row["presentation"]) for row in bulk_grant_routes] == [
        ("admin:bulk-grant", "bulk_grant", "bulk_grant"),
    ]
    a01_f21 = next(feature for feature in features if feature["feature_id"] == "A01-F21")
    assert a01_f21["support_state"] == "needs_device_test"
    assert a01_f21["handler_id"] == "a01.f21.admin.bulk_grant.expiry"
    assert "TG-BOT-SERVER/tests/test_access_store.py::test_bulk_grant_expiry_applies_to_new_group_rights_without_shortening_existing_access" in a01_f21["test_refs"]
    a01_f20 = next(feature for feature in features if feature["feature_id"] == "A01-F20")
    assert a01_f20["support_state"] == "needs_device_test"
    assert a01_f20["handler_id"] == "a01.f20.admin.bulk_revoke.dry_run"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_revoke_confirmation_applies_exact_dry_run_once" in a01_f20["test_refs"]
    bulk_revoke_routes = [row for row in registry["navigation"] if row["page_id"] == "admin.bulk_revoke"]
    assert [(row["callback"], row["label_id"], row["presentation"]) for row in bulk_revoke_routes] == [
        ("admin:bulk-revoke", "bulk_revoke", "bulk_revoke"),
    ]
    audit_filter_route = [row for row in registry["navigation"] if row["parent"] == "admin.audit"]
    assert {(row["callback"], tuple(row["roles"]), row["presentation"]) for row in audit_filter_route} == {
        ("admin:audit:filter", ("owner",), "audit_filter"),
        ("admin:audit:export", ("owner",), "audit_export"),
        ("admin:audit:event:*", ("owner",), "audit_event_row"),
        ("admin:audit:back", ("owner",), "nav"),
    }
    assert [row["callback"] for row in registry["navigation"] if row["parent"] == "admin.audit.event"] == ["admin:audit"]
    permission_profiles = next(feature for feature in features if feature["feature_id"] == "A01-F03")
    assert permission_profiles["support_state"] == "needs_device_test"
    assert permission_profiles["handler_id"] == "a01.f03.admin.permission_profiles"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_named_permission_profiles_are_owner_only_and_reuse_the_confirmed_grant_flow" in permission_profiles["test_refs"]
    assert {
        (row["page_id"], row["parent"], row["callback"], tuple(row["roles"]), row["presentation"])
        for row in registry["navigation"] if row["page_id"].startswith("admin.permission_profiles.")
    } == {
        ("admin.permission_profiles.save", "admin.permission_profiles", "admin:profile:save:*", ("owner",), "admin_permission_profile_save"),
        ("admin.permission_profiles.row", "admin.permission_profiles", "admin:profile:apply:*", ("owner",), "admin_permission_profile_row"),
        ("admin.permission_profiles.page", "admin.permission_profiles", "admin:profiles:page:*", ("owner",), "admin_permission_profile_page"),
        ("admin.permission_profiles.cancel", "admin.permission_profile_prompt", "admin:profile:cancel:*", ("owner",), "admin_permission_profile_cancel"),
        ("admin.permission_profiles.back", "admin.permission_profiles", "admin:user:*", ("owner",), "nav"),
    }
    implemented_ids = {"A01-F01", "A01-F03", "A01-F04", "A01-F05", "A01-F06", "A01-F07", "A01-F08", "A01-F09", "A01-F22", "A01-F23", "A01-F24", "A01-F25", "A01-F26", "A01-F27", "A01-F28", "A01-F29", "A01-F30", "B01-F01", "B01-F02", "B01-F03", "B01-F04", "B01-F05", "B01-F06", "B01-F07", "B01-F08", "B01-F18", "B02-F01", "B02-F02", "B02-F03", "B02-F04", "B02-F05", "B02-F06", "B02-F07", "D01-F01", "D01-F02", "D01-F03", "D01-F04", "D01-F05", "D01-F06", "D01-F07", "D01-F08", "D01-F09", "D01-F10", "D01-F11", "D01-F12", "D01-F13", "D01-F14", "D01-F15", "D01-F16", "D01-F17", "D01-F18", "D01-F19", "D01-F20", "D01-F21", "D01-F22", "D01-F23", "D01-F24", "D01-F25", "D01-F26", "D01-F27", "D01-F28", "D01-F29", "D01-F30", "D02-F01", "D02-F02", "D02-F03", "D02-F04", "D02-F05", "D02-F06", "D02-F07", "D02-F08", "D02-F10", "D02-F13", "D02-F23", "P01-F09", "P01-F25"}
    implemented_ids.add("D03-F27")
    owner_invariant = next(feature for feature in features if feature["feature_id"] == "A01-F27")
    assert owner_invariant["support_state"] == "implemented"
    assert owner_invariant["handler_id"] == "a01.f27.admin.owner_invariant"
    owner_invariant_route = next(page for page in registry["navigation"] if page["page_id"] == "admin.user.owner_invariant")
    assert (owner_invariant_route["callback"], owner_invariant_route["roles"], owner_invariant_route["presentation"]) == (
        "admin:owner-invariant", ["owner"], "admin_owner_invariant",
    )
    policy_snapshot = next(feature for feature in features if feature["feature_id"] == "A01-F28")
    assert policy_snapshot["support_state"] == "implemented"
    assert policy_snapshot["handler_id"] == "a01.f28.admin.rbac_policy_snapshot"
    policy_snapshot_route = next(page for page in registry["navigation"] if page["page_id"] == "admin.user.policy_snapshot")
    assert (policy_snapshot_route["callback"], policy_snapshot_route["roles"], policy_snapshot_route["presentation"]) == (
        "admin:policy-snapshot", ["owner"], "admin_rbac_snapshot",
    )
    policy_compare = next(feature for feature in features if feature["feature_id"] == "A01-F29")
    assert policy_compare["support_state"] == "implemented"
    assert policy_compare["handler_id"] == "a01.f29.admin.rbac_policy_compare"
    policy_compare_route = next(page for page in registry["navigation"] if page["page_id"] == "admin.user.policy_compare")
    assert (policy_compare_route["callback"], policy_compare_route["roles"], policy_compare_route["presentation"]) == (
        "admin:policy-compare", ["owner"], "admin_rbac_compare",
    )
    grant_undo = next(feature for feature in features if feature["feature_id"] == "A01-F30")
    assert grant_undo["support_state"] == "implemented"
    assert grant_undo["handler_id"] == "a01.f30.admin.undo_latest_grant"
    grant_undo_route = next(page for page in registry["navigation"] if page["page_id"] == "admin.user.grant_undo")
    assert (grant_undo_route["callback"], grant_undo_route["roles"], grant_undo_route["presentation"]) == (
        "admin:grant-undo:*", ["owner"], "admin_user_grant_undo",
    )
    implemented_ids.add("A01-F10")
    implemented_ids.add("A01-F11")
    implemented_ids.add("A01-F12")
    implemented_ids.add("A01-F13")
    implemented_ids.add("A01-F14")
    implemented_ids.add("A01-F15")
    implemented_ids.add("A01-F16")
    implemented_ids.add("A01-F17")
    implemented_ids.add("A01-F18")
    implemented_ids.add("A01-F19")
    implemented_ids.add("A01-F20")
    implemented_ids.add("A01-F21")
    implemented_ids.add("B02-F08")
    implemented_ids.add("B02-F09")
    implemented_ids.add("B02-F10")
    pinned_devices = next(feature for feature in features if feature["feature_id"] == "B02-F11")
    assert pinned_devices["support_state"] == "needs_device_test"
    assert pinned_devices["handler_id"] == "b02.f11.devices.pinned_list"
    implemented_ids.add("B02-F11")
    custom_order = next(feature for feature in features if feature["feature_id"] == "B02-F12")
    assert custom_order["support_state"] == "needs_device_test"
    assert custom_order["handler_id"] == "b02.f12.devices.custom_order"
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_owner_device_catalog_custom_order_persists_prunes_stale_and_appends_new_devices" in custom_order["test_refs"]
    implemented_ids.add("B02-F12")
    device_compare = next(feature for feature in features if feature["feature_id"] == "B02-F13")
    assert device_compare["support_state"] == "needs_device_test"
    assert device_compare["handler_id"] == "b02.f13.devices.compare_cards"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_comparison_formats_only_reported_fields_and_marks_stale_capabilities_unknown" in device_compare["test_refs"]
    implemented_ids.add("B02-F13")
    completeness = next(feature for feature in features if feature["feature_id"] == "B02-F14")
    assert completeness["support_state"] == "needs_device_test"
    assert completeness["handler_id"] == "b02.f14.devices.card_completeness"
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_completeness_lists_missing_fields_and_truthful_observation_sources" in completeness["test_refs"]
    implemented_ids.add("B02-F14")
    registration_origin = next(feature for feature in features if feature["feature_id"] == "B02-F15")
    assert registration_origin["support_state"] == "needs_device_test"
    assert registration_origin["handler_id"] == "b02.f15.devices.registration_origin"
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_device_registration_provenance_preserves_origin_and_counts_reregistrations" in registration_origin["test_refs"]
    implemented_ids.add("B02-F15")
    inventory_wizard = next(feature for feature in features if feature["feature_id"] == "B02-F16")
    assert inventory_wizard["support_state"] == "needs_device_test"
    assert inventory_wizard["handler_id"] == "b02.f16.devices.inventory_wizard"
    assert inventory_wizard["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_inventory_wizard_guides_tags_and_typed_fields_without_remote_actions" in inventory_wizard["test_refs"]
    wizard_entry = [row for row in registry["navigation"] if row["page_id"] == "device.inventory_wizard"]
    wizard_actions = [row for row in registry["navigation"] if row["parent"] == "catalog.device_inventory_wizard"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in wizard_entry] == [
        ("catalog:wizard:start:*", ["owner"], "device_inventory_wizard"),
    ]
    assert {(row["callback"], tuple(row["roles"]), row["presentation"]) for row in wizard_actions} == {
        ("catalog:wizard:skip:*", ("owner",), "device_inventory_wizard_action"),
        ("catalog:wizard:cancel:*", ("owner",), "device_inventory_wizard_action"),
    }
    implemented_ids.add("B02-F16")
    private_note = next(feature for feature in features if feature["feature_id"] == "B02-F17")
    assert private_note["support_state"] == "needs_device_test"
    assert private_note["handler_id"] == "b02.f17.devices.private_note"
    assert private_note["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_private_device_note_is_owner_only_and_audit_excludes_note_text" in private_note["test_refs"]
    note_entry = [row for row in registry["navigation"] if row["page_id"] == "device.private_note"]
    note_actions = [row for row in registry["navigation"] if row["parent"] == "catalog.device_private_note"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in note_entry] == [
        ("catalog:note:view:*", ["owner"], "device_private_note"),
    ]
    assert {(row["callback"], tuple(row["roles"])) for row in note_actions} == {
        ("catalog:note:edit:*", ("owner",)),
        ("back:device", ("owner",)),
        ("menu:main", ("owner",)),
    }
    implemented_ids.add("B02-F17")
    typed_fields = next(feature for feature in features if feature["feature_id"] == "B02-F18")
    assert typed_fields["support_state"] == "needs_device_test"
    assert typed_fields["handler_id"] == "b02.f18.devices.typed_inventory_fields"
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_owner_typed_inventory_fields_validate_enum_date_and_boolean" in typed_fields["test_refs"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_typed_inventory_owner_can_define_new_enum_schema_in_telegram" in typed_fields["test_refs"]
    implemented_ids.add("B02-F18")
    manual_place = next(feature for feature in features if feature["feature_id"] == "B02-F19")
    assert manual_place["support_state"] == "needs_device_test"
    assert manual_place["handler_id"] == "b02.f19.devices.manual_place_label"
    assert manual_place["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_manual_device_place_label_is_owner_only_and_explicitly_not_gps" in manual_place["test_refs"]
    place_entry = [row for row in registry["navigation"] if row["page_id"] == "device.place_label"]
    place_cancel = [row for row in registry["navigation"] if row["page_id"] == "catalog.device_place_label.cancel"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in place_entry] == [
        ("catalog:place:edit:*", ["owner"], "device_place_label"),
    ]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in place_cancel] == [
        ("catalog:place:cancel:*", ["owner"], "device_place_label_action"),
    ]
    implemented_ids.add("B02-F19")
    purpose = next(feature for feature in features if feature["feature_id"] == "B02-F20")
    assert purpose["support_state"] == "needs_device_test"
    assert purpose["handler_id"] == "b02.f20.devices.purpose_classification"
    assert purpose["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_device_purpose_is_a_single_allowlisted_owner_classification" in purpose["test_refs"]
    purpose_entry = [row for row in registry["navigation"] if row["page_id"] == "device.purpose"]
    purpose_choices = [row for row in registry["navigation"] if row["parent"] == "catalog.device_purpose"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in purpose_entry] == [
        ("catalog:purpose:view:*", ["owner"], "device_purpose"),
    ]
    assert {row["callback"] for row in purpose_choices} >= {
        "catalog:purpose:set:personal:*", "catalog:purpose:set:test:*",
        "catalog:purpose:set:work:*", "catalog:purpose:set:other:*",
        "catalog:purpose:clear:*", "back:device", "menu:main",
    }
    assert all(row["roles"] == ["owner"] for row in purpose_choices)
    implemented_ids.add("B02-F20")
    inventory_templates = next(feature for feature in features if feature["feature_id"] == "B02-F21")
    assert inventory_templates["support_state"] == "needs_device_test"
    assert inventory_templates["handler_id"] == "b02.f21.devices.inventory_templates"
    assert inventory_templates["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_owner_inventory_template_copies_only_safe_metadata_to_blank_device" in inventory_templates["test_refs"]
    template_entries = [row for row in registry["navigation"] if row["page_id"].startswith("device.inventory_template.")]
    assert {row["callback"] for row in template_entries} == {
        "catalog:template:save:*", "catalog:template:apply:*",
    }
    assert all(row["roles"] == ["owner"] for row in template_entries)
    assert all(row["presentation"] == "device_inventory_template" for row in template_entries)
    template_actions = [row for row in registry["navigation"] if row["page_id"].startswith("catalog.inventory_template.")]
    assert all(row["roles"] == ["owner"] for row in template_actions)
    implemented_ids.add("B02-F21")
    duplicate_candidates = next(feature for feature in features if feature["feature_id"] == "B02-F22")
    assert duplicate_candidates["support_state"] == "needs_device_test"
    assert duplicate_candidates["handler_id"] == "b02.f22.devices.duplicate_candidates"
    assert duplicate_candidates["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_likely_duplicate_detection_requires_same_normalized_hostname_and_platform" in duplicate_candidates["test_refs"]
    duplicate_entry = [row for row in registry["navigation"] if row["page_id"] == "device.duplicate_candidate"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in duplicate_entry] == [
        ("catalog:duplicate:compare:*", ["owner"], "device_duplicate_candidate"),
    ]
    implemented_ids.add("B02-F22")
    inactivity = next(feature for feature in features if feature["feature_id"] == "B02-F23")
    assert inactivity["support_state"] == "needs_device_test"
    assert inactivity["handler_id"] == "b02.f23.devices.aggregate_inactivity_history"
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_inactivity_history_is_aggregate_and_returns_platform_deltas" in inactivity["test_refs"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_can_capture_and_compare_aggregate_inactivity_history" in inactivity["test_refs"]
    implemented_ids.add("B02-F23")
    review_date = next(feature for feature in features if feature["feature_id"] == "B02-F24")
    assert review_date["support_state"] == "needs_device_test"
    assert review_date["handler_id"] == "b02.f24.devices.manual_review_date"
    assert review_date["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_owner_device_review_date_is_strict_persistent_and_clearable" in review_date["test_refs"]
    review_entry = [row for row in registry["navigation"] if row["page_id"] == "device.review_date"]
    review_cancel = [row for row in registry["navigation"] if row["page_id"] == "catalog.device_review_date.cancel"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in review_entry] == [
        ("catalog:review:edit:*", ["owner"], "device_review_date"),
    ]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in review_cancel] == [
        ("catalog:review:cancel:*", ["owner"], "device_review_date_action"),
    ]
    implemented_ids.add("B02-F24")
    maintenance = next(feature for feature in features if feature["feature_id"] == "B02-F25")
    assert maintenance["support_state"] == "needs_device_test"
    assert maintenance["handler_id"] == "b02.f25.devices.maintenance_mode"
    assert maintenance["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_device_maintenance_is_owner_only_expires_and_excludes_only_selected_aggregate" in maintenance["test_refs"]
    maintenance_entry = [row for row in registry["navigation"] if row["page_id"] == "device.maintenance"]
    maintenance_cancel = [row for row in registry["navigation"] if row["page_id"] == "catalog.device_maintenance.cancel"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in maintenance_entry] == [
        ("catalog:maintenance:edit:*", ["owner"], "device_maintenance"),
    ]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in maintenance_cancel] == [
        ("catalog:maintenance:cancel:*", ["owner"], "device_maintenance_action"),
    ]
    implemented_ids.add("B02-F25")
    selected_snapshot = next(feature for feature in features if feature["feature_id"] == "B02-F26")
    assert selected_snapshot["support_state"] == "needs_device_test"
    assert selected_snapshot["handler_id"] == "b02.f26.devices.selected_snapshot_ttl"
    assert selected_snapshot["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_selected_device_snapshot_expires_and_reports_stale_ids" in selected_snapshot["test_refs"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_selected_device_snapshot_ui_entry_is_owner_only" in selected_snapshot["test_refs"]
    implemented_ids.add("B02-F26")
    archived = next(feature for feature in features if feature["feature_id"] == "B02-F27")
    assert archived["support_state"] == "needs_device_test"
    assert archived["handler_id"] == "b02.f27.devices.archive_restore"
    assert archived["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_store.py::test_device_archive_is_owner_only_reversible_and_does_not_remove_device" in archived["test_refs"]
    archive_entry = [row for row in registry["navigation"] if row["page_id"] == "device.archive"]
    archive_list = [row for row in registry["navigation"] if row["page_id"] == "devices.archive"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in archive_entry] == [
        ("catalog:archive:set:*", ["owner"], "device_archive"),
    ]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in archive_list] == [
        ("catalog:archive:list", ["owner"], "devices_list_action"),
    ]
    implemented_ids.add("B02-F27")
    exported = next(feature for feature in features if feature["feature_id"] == "B02-F28")
    assert exported["support_state"] == "needs_device_test"
    assert exported["handler_id"] == "b02.f28.devices.explicit_field_export"
    assert exported["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_export_includes_only_explicit_allowlisted_fields" in exported["test_refs"]
    export_entry = [row for row in registry["navigation"] if row["page_id"] == "devices.export"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in export_entry] == [
        ("catalog:export", ["owner"], "devices_list_action"),
    ]
    implemented_ids.add("B02-F28")
    imported = next(feature for feature in features if feature["feature_id"] == "B02-F29")
    assert imported["support_state"] == "needs_device_test"
    assert imported["handler_id"] == "b02.f29.devices.read_only_import_reconcile"
    assert imported["role"] == ["owner"]
    assert "TG-BOT-SERVER/tests/test_device_catalog_import.py::test_json_import_reports_new_missing_and_field_conflicts_without_mutating_current" in imported["test_refs"]
    import_entry = [row for row in registry["navigation"] if row["page_id"] == "devices.import"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in import_entry] == [
        ("catalog:import", ["owner"], "devices_list_action"),
    ]
    import_cancel = [row for row in registry["navigation"] if row["page_id"] == "catalog.device_import.cancel"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in import_cancel] == [
        ("catalog:import:cancel", ["owner"], "device_catalog_import_action"),
    ]
    implemented_ids.add("B02-F29")
    relations = next(feature for feature in features if feature["feature_id"] == "B02-F30")
    assert relations["support_state"] == "needs_device_test"
    assert relations["handler_id"] == "b02.f30.devices.manual_relations"
    assert relations["role"] == ["owner"]
    relation_entry = [row for row in registry["navigation"] if row["page_id"] == "device.links"]
    relation_actions = [row for row in registry["navigation"] if row["parent"] == "catalog.device_links.view"]
    assert [(row["callback"], row["roles"], row["presentation"]) for row in relation_entry] == [
        ("catalog:link:manage:*", ["owner"], "device_relations"),
    ]
    assert {(row["callback"], tuple(row["roles"]), row["presentation"]) for row in relation_actions} == {
        ("catalog:link:add:*", ("owner",), "device_relation_action"),
        ("catalog:link:unlink:*", ("owner",), "device_relation_action"),
        ("catalog:link:cancel:*", ("owner",), "device_relation_action"),
    }
    implemented_ids.add("B02-F30")
    implemented_ids.add("D02-F11")
    implemented_ids.add("D02-F12")
    implemented_ids.add("D02-F14")
    implemented_ids.add("D02-F15")
    implemented_ids.add("D02-F16")
    implemented_ids.add("D02-F19")
    implemented_ids.add("D02-F20")
    implemented_ids.add("D02-F17")
    implemented_ids.add("D02-F18")
    implemented_ids.add("D02-F21")
    media_cancel = next(feature for feature in features if feature["feature_id"] == "D02-F22")
    assert media_cancel["support_state"] == "needs_device_test"
    assert media_cancel["handler_id"] == "d02.f22.media.request.cancel"
    assert media_cancel["role"] == ["owner"]
    assert any("test_media_series_cancel_is_owner_only_correlated_and_suppresses_preview" in ref for ref in media_cancel["test_refs"])
    implemented_ids.add("D02-F22")
    media_history = next(feature for feature in features if feature["feature_id"] == "D02-F24")
    assert media_history["support_state"] == "needs_device_test"
    assert media_history["handler_id"] == "d02.f24.media.history.metadata_only"
    assert media_history["role"] == ["owner"]
    assert any("test_media_history_is_owner_only_correlated_metadata_and_edits_one_card" in ref for ref in media_history["test_refs"])
    media_history_nav = next(row for row in registry["navigation"] if row["callback"] == "media:history")
    assert media_history_nav["parent"] == "mic.options" and media_history_nav["roles"] == ["owner"]
    implemented_ids.add("D02-F24")
    implemented_ids.add("D04-F05")
    implemented_ids.add("D04-F06")
    implemented_ids.add("D04-F07")
    implemented_ids.add("D04-F08")
    implemented_ids.add("D04-F09")
    mtu = next(feature for feature in features if feature["feature_id"] == "D04-F10")
    assert mtu["support_state"] == "needs_device_test"
    assert mtu["handler_id"] == "d04.f10.network.path_mtu"
    assert mtu["role"] == ["owner"]
    assert set(mtu["test_refs"]) == {
        "XGENT-WDS/tests/test_path_mtu.py::test_agent_publishes_correlated_path_mtu_result",
        "XGENT-MCS/tests/test_path_mtu.py::test_agent_publishes_correlated_path_mtu_result",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_path_mtu_is_owner_only_correlated_and_edits_one_card",
    }
    mtu_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:path_mtu")
    assert (mtu_nav["parent"], mtu_nav["roles"], mtu_nav["presentation"]) == (
        "network", ["owner"], "network_path_mtu",
    )
    implemented_ids.add("D04-F10")
    captive_portal = next(feature for feature in features if feature["feature_id"] == "D04-F11")
    assert captive_portal["support_state"] == "needs_device_test"
    assert captive_portal["handler_id"] == "d04.f11.network.captive_portal"
    assert captive_portal["role"] == ["owner"]
    assert set(captive_portal["test_refs"]) == {
        "XGENT-WDS/tests/test_captive_portal.py::test_agent_publishes_correlated_captive_portal_status",
        "XGENT-MCS/tests/test_captive_portal.py::test_agent_publishes_correlated_captive_portal_status",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_captive_portal_is_owner_only_correlated_and_edits_one_card",
    }
    captive_portal_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:captive_portal")
    assert (captive_portal_nav["parent"], captive_portal_nav["roles"], captive_portal_nav["presentation"]) == (
        "network", ["owner"], "network_captive_portal",
    )
    implemented_ids.add("D04-F11")
    ipv6 = next(feature for feature in features if feature["feature_id"] == "D04-F12")
    assert ipv6["support_state"] == "needs_device_test"
    assert ipv6["handler_id"] == "d04.f12.network.ipv6_connectivity"
    assert ipv6["role"] == ["owner"]
    assert set(ipv6["test_refs"]) == {
        "XGENT-WDS/tests/test_ipv6_connectivity.py::test_agent_publishes_correlated_ipv6_reachability",
        "XGENT-MCS/tests/test_ipv6_connectivity.py::test_agent_publishes_correlated_ipv6_reachability",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_ipv6_connectivity_is_owner_only_correlated_and_edits_one_card",
    }
    ipv6_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:ipv6_connectivity")
    assert (ipv6_nav["parent"], ipv6_nav["roles"], ipv6_nav["presentation"]) == (
        "network", ["owner"], "network_ipv6_connectivity",
    )
    implemented_ids.add("D04-F12")
    tls_cert = next(feature for feature in features if feature["feature_id"] == "D04-F13")
    assert tls_cert["support_state"] == "needs_device_test"
    assert tls_cert["handler_id"] == "d04.f13.network.tls_certificate"
    assert tls_cert["role"] == ["owner"]
    assert set(tls_cert["test_refs"]) == {
        "XGENT-WDS/tests/test_tls_certificate.py::test_agent_publishes_correlated_certificate_result",
        "XGENT-MCS/tests/test_tls_certificate.py::test_agent_publishes_correlated_certificate_result",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_tls_certificate_is_owner_only_correlated_and_replaces_the_current_card",
    }
    tls_cert_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:tls_certificate")
    assert (tls_cert_nav["parent"], tls_cert_nav["roles"], tls_cert_nav["presentation"]) == (
        "network", ["owner"], "network_tls_certificate",
    )
    implemented_ids.add("D04-F13")
    service_probe = next(feature for feature in features if feature["feature_id"] == "D04-F14")
    assert service_probe["support_state"] == "needs_device_test"
    assert service_probe["handler_id"] == "d04.f14.network.service_probe"
    assert service_probe["role"] == ["owner"]
    assert set(service_probe["test_refs"]) == {
        "XGENT-WDS/tests/test_service_probe.py::test_agent_publishes_correlated_service_status",
        "XGENT-MCS/tests/test_service_probe.py::test_agent_publishes_correlated_service_status",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_service_probe_is_owner_only_and_replaces_the_current_card",
    }
    service_probe_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:service_probe")
    assert (service_probe_nav["parent"], service_probe_nav["roles"], service_probe_nav["presentation"]) == (
        "network", ["owner"], "network_service_probe",
    )
    implemented_ids.add("D04-F14")
    wifi_quality = next(feature for feature in features if feature["feature_id"] == "D04-F15")
    assert wifi_quality["support_state"] == "needs_device_test"
    assert wifi_quality["handler_id"] == "d04.f15.network.wifi_quality_history"
    assert wifi_quality["role"] == ["owner"]
    assert set(wifi_quality["test_refs"]) == {
        "XGENT-WDS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_quality_sample_without_network_identity",
        "XGENT-MCS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_quality_sample_without_network_identity",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_wifi_quality_history_is_owner_only_persists_one_sample_and_edits_card",
    }
    wifi_quality_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:wifi_quality_history")
    assert (wifi_quality_nav["parent"], wifi_quality_nav["roles"], wifi_quality_nav["presentation"]) == (
        "network", ["owner"], "network_wifi_quality_history",
    )
    implemented_ids.add("D04-F15")
    wifi_channel = next(feature for feature in features if feature["feature_id"] == "D04-F16")
    assert wifi_channel["support_state"] == "needs_device_test"
    assert wifi_channel["handler_id"] == "d04.f16.network.wifi_channel_summary"
    assert wifi_channel["role"] == ["owner"]
    assert set(wifi_channel["test_refs"]) == {
        "XGENT-WDS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_channel_summary",
        "XGENT-MCS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_channel_summary",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_wifi_channel_summary_is_owner_only_correlated_and_replaces_the_current_card",
    }
    wifi_channel_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:wifi_channel_summary")
    assert (wifi_channel_nav["parent"], wifi_channel_nav["roles"], wifi_channel_nav["presentation"]) == (
        "network", ["owner"], "network_wifi_channel_summary",
    )
    implemented_ids.add("D04-F16")
    wifi_history = next(feature for feature in features if feature["feature_id"] == "D04-F17")
    assert wifi_history["support_state"] == "needs_device_test"
    assert wifi_history["handler_id"] == "d04.f17.network.wifi_connection_history"
    assert wifi_history["role"] == ["owner"]
    assert set(wifi_history["test_refs"]) == {
        "XGENT-WDS/tests/test_wifi_connection_history.py::test_agent_publishes_correlated_wifi_connection_history_without_event_payloads",
        "XGENT-MCS/tests/test_wifi_connection_history.py::test_agent_publishes_correlated_wifi_connection_history_without_event_payloads",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_wifi_connection_history_is_owner_only_and_edits_the_same_card",
    }
    wifi_history_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:wifi_connection_history")
    assert (wifi_history_nav["parent"], wifi_history_nav["roles"], wifi_history_nav["presentation"]) == (
        "network", ["owner"], "network_wifi_connection_history",
    )
    implemented_ids.add("D04-F17")
    traffic = next(feature for feature in features if feature["feature_id"] == "D04-F18")
    assert traffic["support_state"] == "needs_device_test"
    assert traffic["handler_id"] == "d04.f18.network.interface_traffic"
    assert traffic["role"] == ["owner"]
    assert set(traffic["test_refs"]) == {
        "XGENT-WDS/tests/test_network_traffic.py::test_windows_agent_publishes_correlated_selected_adapter_traffic",
        "XGENT-MCS/tests/test_network_traffic.py::test_macos_agent_publishes_correlated_selected_adapter_traffic",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_traffic_is_owner_only_and_edits_the_same_card",
    }
    traffic_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:network_traffic")
    assert (traffic_nav["parent"], traffic_nav["roles"], traffic_nav["presentation"]) == (
        "network", ["owner"], "network_interface_traffic",
    )
    implemented_ids.add("D04-F18")
    traffic_alert = next(feature for feature in features if feature["feature_id"] == "D04-F19")
    assert traffic_alert["support_state"] == "needs_device_test"
    assert traffic_alert["handler_id"] == "d04.f19.network.traffic_threshold_alert"
    assert traffic_alert["role"] == ["owner"]
    assert set(traffic_alert["test_refs"]) == {
        "XGENT-WDS/tests/test_network_traffic.py::test_windows_agent_acknowledges_owner_selected_threshold_schedule",
        "XGENT-MCS/tests/test_network_traffic.py::test_macos_agent_acknowledges_owner_selected_threshold_schedule",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_traffic_alert_sample_requires_exact_fresh_selected_interface",
    }
    implemented_ids.add("D04-F19")
    interface_history = next(feature for feature in features if feature["feature_id"] == "D04-F20")
    assert interface_history["support_state"] == "needs_device_test"
    assert interface_history["handler_id"] == "d04.f20.network.interface_status_history"
    assert interface_history["role"] == ["owner"]
    assert set(interface_history["test_refs"]) == {
        "XGENT-WDS/tests/test_interface_status_history.py::test_history_records_only_verified_state_transitions_and_bounds_rows",
        "XGENT-MCS/tests/test_interface_status_history.py::test_history_records_only_verified_state_transitions_and_bounds_rows",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_interface_status_history_validator_requires_fresh_exact_transition_rows",
    }
    implemented_ids.add("D04-F20")
    proxy_status = next(feature for feature in features if feature["feature_id"] == "D04-F23")
    assert proxy_status["support_state"] == "needs_device_test"
    assert proxy_status["handler_id"] == "d04.f23.network.proxy_status"
    assert proxy_status["role"] == ["owner"]
    assert proxy_status["baseline_evidence"] == [{
        "path": SPEC,
        "reference": "D04-F23 baseline audit",
        "novelty_status": "confirmed_absent",
    }]
    assert set(proxy_status["test_refs"]) == {
        "XGENT-WDS/tests/test_proxy_status.py::test_agent_publishes_correlated_redacted_proxy_status",
        "XGENT-MCS/tests/test_proxy_status.py::test_agent_publishes_correlated_redacted_proxy_status",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_proxy_status_is_owner_only_and_edits_the_current_card",
    }
    proxy_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:proxy_status")
    assert (proxy_nav["parent"], proxy_nav["roles"], proxy_nav["presentation"]) == (
        "network", ["owner"], "network_proxy_status",
    )
    implemented_ids.add("D04-F23")
    vpn_route = next(feature for feature in features if feature["feature_id"] == "D04-F24")
    assert vpn_route["support_state"] == "needs_device_test"
    assert vpn_route["handler_id"] == "d04.f24.network.vpn_route_status"
    assert vpn_route["role"] == ["owner"]
    assert vpn_route["baseline_evidence"] == [{
        "path": "docs/superpowers/specs/2026-10-14-tarped-capability-expansion-design.md",
        "reference": "D04-F24 baseline audit",
        "novelty_status": "confirmed_absent",
    }]
    assert set(vpn_route["test_refs"]) == {
        "XGENT-WDS/tests/test_vpn_route.py::test_agent_publishes_correlated_vpn_route_summary",
        "XGENT-MCS/tests/test_vpn_route.py::test_agent_publishes_correlated_vpn_route_summary",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_vpn_route_status_is_owner_only_and_edits_the_current_card",
    }
    vpn_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:vpn_route_status")
    assert (vpn_nav["parent"], vpn_nav["roles"], vpn_nav["presentation"]) == (
        "network", ["owner"], "network_vpn_route_status",
    )
    implemented_ids.add("D04-F24")
    endpoint_batch = next(feature for feature in features if feature["feature_id"] == "D04-F25")
    assert endpoint_batch["support_state"] == "needs_device_test"
    assert endpoint_batch["handler_id"] == "d04.f25.network.endpoint_batch"
    assert endpoint_batch["role"] == ["owner"]
    assert endpoint_batch["baseline_evidence"] == [{
        "path": "docs/superpowers/specs/2026-10-14-tarped-capability-expansion-design.md",
        "reference": "D04-F25 baseline audit",
        "novelty_status": "confirmed_absent",
    }]
    assert set(endpoint_batch["test_refs"]) == {
        "XGENT-WDS/tests/test_endpoint_batch.py::test_agent_publishes_correlated_bounded_endpoint_set",
        "XGENT-MCS/tests/test_endpoint_batch.py::test_agent_publishes_correlated_bounded_endpoint_set",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_endpoint_batch_is_owner_only_fixed_and_edits_the_current_card",
    }
    endpoints_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:endpoint_batch")
    assert (endpoints_nav["parent"], endpoints_nav["roles"], endpoints_nav["presentation"]) == (
        "network", ["owner"], "network_endpoint_batch",
    )
    implemented_ids.add("D04-F25")
    dnssec = next(feature for feature in features if feature["feature_id"] == "D04-F26")
    assert dnssec["support_state"] == "needs_device_test"
    assert dnssec["handler_id"] == "d04.f26.network.dnssec_check"
    assert dnssec["role"] == ["owner"]
    assert dnssec["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert {"XGENT-WDS/tests/test_dnssec_check.py::test_agent_publishes_correlated_dnssec_assessment",
            "XGENT-MCS/tests/test_dnssec_check.py::test_agent_publishes_correlated_dnssec_assessment",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_dnssec_check_is_owner_only_and_edits_current_card"} == set(dnssec["test_refs"])
    dnssec_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:dnssec_check")
    assert (dnssec_nav["parent"], dnssec_nav["roles"], dnssec_nav["presentation"]) == (
        "network", ["owner"], "network_dnssec_check",
    )
    implemented_ids.add("D04-F26")
    path_diagnostic = next(feature for feature in features if feature["feature_id"] == "D04-F27")
    assert path_diagnostic["support_state"] == "needs_device_test"
    assert path_diagnostic["handler_id"] == "d04.f27.network.path_diagnostic"
    assert path_diagnostic["role"] == ["owner"]
    assert path_diagnostic["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(path_diagnostic["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_path_diagnostic_runs_fixed_correlated_probes_and_edits_one_card",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_path_diagnostic_labels_only_confirmed_failures",
    }
    diagnostic_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:path_diagnostic")
    assert (diagnostic_nav["parent"], diagnostic_nav["roles"], diagnostic_nav["presentation"]) == (
        "network", ["owner"], "network_path_diagnostic",
    )
    implemented_ids.add("D04-F27")
    network_report = next(feature for feature in features if feature["feature_id"] == "D04-F28")
    assert network_report["support_state"] == "needs_device_test"
    assert network_report["handler_id"] == "d04.f28.network.report_export"
    assert network_report["role"] == ["owner"]
    assert network_report["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert "file" in network_report["result_types"]
    assert set(network_report["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_path_report_is_editable_json_with_allowlisted_fields_only",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_path_report_export_is_owner_bound_and_consumed_once",
    }
    implemented_ids.add("D04-F28")
    network_check_sets = next(feature for feature in features if feature["feature_id"] == "D04-F29")
    assert network_check_sets["support_state"] == "needs_device_test"
    assert network_check_sets["handler_id"] == "d04.f29.network.check_sets"
    assert network_check_sets["role"] == ["owner"]
    assert network_check_sets["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(network_check_sets["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_device_store.py::test_network_check_sets_are_owner_only_bounded_and_persist",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_check_set_callbacks_are_owner_only_and_registered",
    }
    check_sets_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:network_check_sets")
    assert (check_sets_nav["parent"], check_sets_nav["roles"], check_sets_nav["presentation"]) == (
        "network", ["owner"], "network_check_sets",
    )
    implemented_ids.add("D04-F29")
    latency_series = next(feature for feature in features if feature["feature_id"] == "D04-F01")
    assert latency_series["support_state"] == "needs_device_test"
    assert latency_series["handler_id"] == "d04.f01.network.latency_series"
    assert latency_series["role"] == ["owner"]
    assert latency_series["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(latency_series["test_refs"]) == {
        "XGENT-WDS/tests/test_latency_series.py::test_agent_publishes_correlated_series_from_fixed_five_attempt_contract",
        "XGENT-MCS/tests/test_latency_series.py::test_agent_publishes_correlated_series_from_fixed_five_attempt_contract",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_latency_series_owner_flow_is_correlated_and_edits_existing_card",
    }
    latency_series_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:latency_series")
    assert (latency_series_nav["parent"], latency_series_nav["roles"], latency_series_nav["presentation"]) == (
        "network", ["owner"], "network_latency_series",
    )
    implemented_ids.add("D04-F01")
    packet_loss = next(feature for feature in features if feature["feature_id"] == "D04-F02")
    assert packet_loss["support_state"] == "needs_device_test"
    assert packet_loss["handler_id"] == "d04.f02.network.packet_loss_history"
    assert packet_loss["role"] == ["owner"]
    assert packet_loss["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(packet_loss["test_refs"]) == {
        "XGENT-WDS/tests/test_packet_loss_history.py::test_agent_publishes_correlated_packet_loss_history",
        "XGENT-MCS/tests/test_packet_loss_history.py::test_agent_publishes_correlated_packet_loss_history",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_packet_loss_history_owner_flow_is_single_device_owner_only_and_correlated",
    }
    packet_loss_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:packet_loss_history")
    assert (packet_loss_nav["parent"], packet_loss_nav["roles"], packet_loss_nav["presentation"]) == (
        "network", ["owner"], "network_packet_loss_history",
    )
    implemented_ids.add("D04-F02")
    jitter = next(feature for feature in features if feature["feature_id"] == "D04-F03")
    assert jitter["support_state"] == "needs_device_test"
    assert jitter["handler_id"] == "d04.f03.network.jitter"
    assert jitter["role"] == ["owner"]
    assert jitter["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(jitter["test_refs"]) == {
        "XGENT-WDS/tests/test_jitter_measurement.py::test_agent_publishes_correlated_icmp_jitter_result",
        "XGENT-MCS/tests/test_jitter_measurement.py::test_agent_publishes_correlated_icmp_jitter_result",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_jitter_owner_flow_is_single_device_owner_only_and_correlated",
    }
    jitter_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:network_jitter")
    assert (jitter_nav["parent"], jitter_nav["roles"], jitter_nav["presentation"]) == (
        "network", ["owner"], "network_jitter",
    )
    implemented_ids.add("D04-F03")
    throughput = next(feature for feature in features if feature["feature_id"] == "D04-F04")
    assert throughput["support_state"] == "needs_device_test"
    assert throughput["handler_id"] == "d04.f04.network.throughput"
    assert throughput["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(throughput["test_refs"]) == {
        "XGENT-WDS/tests/test_network_throughput.py::test_agent_publishes_correlated_throughput_result",
        "XGENT-MCS/tests/test_network_throughput.py::test_agent_publishes_correlated_throughput_result",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_throughput_owner_flow_is_single_device_owner_only_and_correlated",
    }
    throughput_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:network_throughput")
    assert (throughput_nav["parent"], throughput_nav["roles"], throughput_nav["presentation"]) == (
        "network", ["owner"], "network_throughput",
    )
    implemented_ids.add("D04-F04")
    link_speed = next(feature for feature in features if feature["feature_id"] == "D04-F21")
    assert link_speed["support_state"] == "needs_device_test"
    assert link_speed["handler_id"] == "d04.f21.network.link_speed"
    assert link_speed["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(link_speed["test_refs"]) == {
        "XGENT-WDS/tests/test_network_link_speed.py::test_agent_publishes_correlated_link_speed_result",
        "XGENT-MCS/tests/test_network_link_speed.py::test_agent_publishes_correlated_link_speed_result",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_link_speed_owner_flow_selects_one_interface_and_edits_card",
    }
    link_speed_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:network_link_speed")
    assert (link_speed_nav["parent"], link_speed_nav["roles"], link_speed_nav["presentation"]) == (
        "network", ["owner"], "network_link_speed",
    )
    implemented_ids.add("D04-F21")
    route_lookup = next(feature for feature in features if feature["feature_id"] == "D04-F22")
    assert route_lookup["support_state"] == "needs_device_test"
    assert route_lookup["handler_id"] == "d04.f22.network.route_lookup"
    assert route_lookup["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(route_lookup["test_refs"]) == {
        "XGENT-WDS/tests/test_route_lookup.py::test_agent_publishes_correlated_route_lookup",
        "XGENT-MCS/tests/test_route_lookup.py::test_agent_publishes_correlated_route_lookup",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_route_lookup_is_owner_only_correlated_and_edits_one_card",
    }
    route_lookup_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:route_lookup")
    assert (route_lookup_nav["parent"], route_lookup_nav["roles"], route_lookup_nav["presentation"]) == (
        "network", ["owner"], "network_route_lookup",
    )
    implemented_ids.add("D04-F22")
    shell_timing = next(feature for feature in features if feature["feature_id"] == "D05-F28")
    assert shell_timing["support_state"] == "needs_device_test"
    assert shell_timing["handler_id"] == "d05.f28.terminal.shell_timing_report"
    assert shell_timing["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_timing["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_access_store.py::test_shell_timing_summary_groups_only_the_two_approved_intervals",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_timing_report_is_owner_only_aggregate_and_edits_one_card",
    }
    shell_timing_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_timing_report")
    assert (shell_timing_nav["parent"], shell_timing_nav["roles"], shell_timing_nav["presentation"]) == (
        "terminal", ["owner"], "terminal_shell_timing",
    )
    implemented_ids.add("D05-F28")
    shell_runner = next(feature for feature in features if feature["feature_id"] == "D05-F29")
    assert shell_runner["support_state"] == "needs_device_test"
    assert shell_runner["handler_id"] == "d05.f29.terminal.shell_runner_diagnostic"
    assert shell_runner["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert "XGENT-MCS/tests/test_capabilities.py::test_shell_runner_probe_checks_filesystem_only_and_never_starts_a_process" in shell_runner["test_refs"]
    shell_runner_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_runner_diagnostic")
    assert (shell_runner_nav["parent"], shell_runner_nav["roles"], shell_runner_nav["presentation"]) == (
        "terminal", ["owner"], "terminal_shell_runner_diagnostic",
    )
    implemented_ids.add("D05-F29")
    shell_inbox = next(feature for feature in features if feature["feature_id"] == "D05-F19")
    assert shell_inbox["support_state"] == "needs_device_test"
    assert shell_inbox["handler_id"] == "d05.f19.terminal.shell_approval_inbox"
    assert shell_inbox["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_inbox["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_command_approval.py::test_pending_inbox_lists_only_fresh_requests_for_the_captured_owner",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_approval_inbox_is_owner_only_read_only_and_shows_exact_preview",
    }
    shell_inbox_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_approval_inbox")
    assert (shell_inbox_nav["parent"], shell_inbox_nav["roles"], shell_inbox_nav["presentation"]) == (
        "terminal", ["owner"], "terminal_shell_approval_inbox",
    )
    implemented_ids.add("D05-F19")
    implemented_ids.add("D05-F20")
    shell_dry_run = next(feature for feature in features if feature["feature_id"] == "D05-F21")
    assert shell_dry_run["support_state"] == "needs_device_test"
    assert shell_dry_run["handler_id"] == "d05.f21.terminal.dry_run_preview"
    assert shell_dry_run["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_dry_run["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_dry_run.py::test_builds_native_dry_run_for_allowlisted_commands",
        "TG-BOT-SERVER/tests/test_command_approval.py::test_dry_run_replacement_invalidates_original_and_keeps_original_expiry",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_dry_run_replaces_preview_then_requires_fresh_owner_approval",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_approval_offers_dry_run_only_for_supported_mutating_commands",
    }
    implemented_ids.add("D05-F21")
    shell_runbook = next(feature for feature in features if feature["feature_id"] == "D05-F22")
    assert shell_runbook["support_state"] == "needs_device_test"
    assert shell_runbook["handler_id"] == "d05.f22.terminal.owner_runbook"
    assert shell_runbook["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_runbook["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_runbook.py::test_runbook_starts_only_after_owner_confirmation_and_tracks_exact_order",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_confirmation_creates_only_first_exact_owner_approval",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_success_requires_fresh_approval_for_next_step_and_failure_stops",
    }
    runbook_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_runbook")
    assert (runbook_nav["parent"], runbook_nav["roles"], runbook_nav["presentation"]) == (
        "terminal", ["owner"], "terminal_shell_runbook",
    )
    implemented_ids.add("D05-F22")
    runbook_report = next(feature for feature in features if feature["feature_id"] == "D05-F23")
    assert runbook_report["support_state"] == "needs_device_test"
    assert runbook_report["handler_id"] == "d05.f23.terminal.runbook_step_report"
    assert runbook_report["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(runbook_report["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_runbook.py::test_runbook_report_records_terminal_outcome_duration_and_exit_code_without_commands",
        "TG-BOT-SERVER/tests/test_shell_runbook.py::test_runbook_report_marks_unknown_result_and_unstarted_step_truthfully",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_success_requires_fresh_approval_for_next_step_and_failure_stops",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_nonzero_exit_stops_without_creating_next_pending_step",
    }
    implemented_ids.add("D05-F23")
    shell_compare = next(feature for feature in features if feature["feature_id"] == "D05-F24")
    assert shell_compare["support_state"] == "needs_device_test"
    assert shell_compare["handler_id"] == "d05.f24.terminal.shell_result_compare"
    assert shell_compare["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_compare["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_result_compare.py::test_manual_selection_compares_only_redacted_results_and_is_single_use",
        "TG-BOT-SERVER/tests/test_shell_result_compare.py::test_selection_is_owner_scoped_and_expired_result_cannot_be_compared",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_result_comparison_requires_two_manual_owner_selections",
    }
    implemented_ids.add("D05-F24")
    shell_repeat_request = next(feature for feature in features if feature["feature_id"] == "D05-F25")
    assert shell_repeat_request["support_state"] == "needs_device_test"
    assert shell_repeat_request["handler_id"] == "d05.f25.terminal.repeat_request"
    assert shell_repeat_request["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_repeat_request["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_repeat.py::test_repeat_is_owner_scoped_short_lived_and_consumed_once",
        "TG-BOT-SERVER/tests/test_shell_repeat.py::test_repeat_candidates_require_a_terminal_execution_result",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_repeat_creates_a_fresh_approval_without_publishing",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_repeat_callback_route_is_owner_gated",
    }
    implemented_ids.add("D05-F25")
    shell_templates = next(feature for feature in features if feature["feature_id"] == "D05-F26")
    assert shell_templates["support_state"] == "needs_device_test"
    assert shell_templates["handler_id"] == "d05.f26.terminal.shell_runbook_templates"
    assert shell_templates["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_templates["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_device_store.py::test_shell_runbook_templates_are_owner_only_persistent_and_separate_from_execution_plans",
        "TG-BOT-SERVER/tests/test_device_store.py::test_shell_runbook_template_rename_and_delete_change_only_template_library",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_runbook_template_library_is_owner_only_and_visible_in_terminal_menu",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_using_saved_runbook_template_only_creates_draft_and_keeps_step_approval",
    }
    templates_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_runbook_templates")
    assert (templates_nav["parent"], templates_nav["roles"], templates_nav["presentation"]) == (
        "terminal", ["owner"], "terminal_shell_runbook_templates",
    )
    implemented_ids.add("D05-F26")
    shell_metadata_export = next(feature for feature in features if feature["feature_id"] == "D05-F27")
    assert shell_metadata_export["support_state"] == "needs_device_test"
    assert shell_metadata_export["handler_id"] == "d05.f27.terminal.shell_metadata_export"
    assert shell_metadata_export["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_metadata_export["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_access_store.py::test_owner_shell_audit_export_filters_dates_and_status_without_command_content",
        "TG-BOT-SERVER/tests/test_access_store.py::test_owner_shell_audit_export_rejects_non_owner_invalid_status_and_unbounded_range",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_metadata_export_parser_requires_bounded_date_range_and_known_statuses",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_metadata_export_delivers_owner_scoped_filtered_json_without_command_data",
    }
    metadata_export_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:shell_metadata_export")
    assert (metadata_export_nav["parent"], metadata_export_nav["roles"], metadata_export_nav["presentation"]) == (
        "terminal", ["owner"], "terminal_shell_metadata_export",
    )
    implemented_ids.add("D05-F27")
    shell_queue = next(feature for feature in features if feature["feature_id"] == "D05-F17")
    assert shell_queue["support_state"] == "needs_device_test"
    assert shell_queue["handler_id"] == "d05.f17.terminal.device_command_queue"
    assert shell_queue["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert "TG-BOT-SERVER/tests/test_shell_command_queue.py::test_fifo_is_per_device_and_release_wakes_next_ticket" in shell_queue["test_refs"]
    implemented_ids.add("D05-F17")
    shell_capacity = next(feature for feature in features if feature["feature_id"] == "D05-F18")
    assert shell_capacity["support_state"] == "needs_device_test"
    assert shell_capacity["handler_id"] == "d05.f18.terminal.owner_task_capacity"
    assert shell_capacity["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert "TG-BOT-SERVER/tests/test_shell_command_queue.py::test_global_task_cap_fairly_queues_across_devices_and_releases_capacity" in shell_capacity["test_refs"]
    implemented_ids.add("D05-F18")
    redactor_summary = next(feature for feature in features if feature["feature_id"] == "D05-F16")
    assert redactor_summary["support_state"] == "implemented"
    assert redactor_summary["handler_id"] == "d05.f16.terminal.redactor_summary"
    assert redactor_summary["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(redactor_summary["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_access_store.py::test_shell_redactor_summary_counts_real_categories_without_storing_secret_values",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_result_reports_only_redaction_counts_and_categories",
    }
    implemented_ids.add("D05-F16")
    redacted_preview = next(feature for feature in features if feature["feature_id"] == "D05-F14")
    assert redacted_preview["support_state"] == "implemented"
    assert redacted_preview["handler_id"] == "d05.f14.terminal.redacted_result_preview"
    assert redacted_preview["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(redacted_preview["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_redacted_output_requires_owner_preview_tap",
    }
    implemented_ids.add("D05-F14")
    json_shell_view = next(feature for feature in features if feature["feature_id"] == "D05-F15")
    assert json_shell_view["support_state"] == "needs_device_test"
    assert json_shell_view["handler_id"] == "d05.f15.terminal.json_fields"
    assert json_shell_view["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(json_shell_view["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_result_pager.py::test_json_fields_are_flattened_only_after_redaction_and_bounded",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_valid_json_shell_result_can_switch_between_fields_and_redacted_source",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_secret_redaction_happens_before_json_view_detection",
    }
    implemented_ids.add("D05-F15")
    shell_pagination = next(feature for feature in features if feature["feature_id"] == "D05-F13")
    assert shell_pagination["support_state"] == "needs_device_test"
    assert shell_pagination["handler_id"] == "d05.f13.terminal.result_pagination"
    assert shell_pagination["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(shell_pagination["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_shell_result_pager.py::test_result_pages_are_bounded_owner_scoped_and_expire",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_long_shell_result_pages_through_the_same_owner_card",
    }
    implemented_ids.add("D05-F13")
    external_ip_watch = next(feature for feature in features if feature["feature_id"] == "D04-F30")
    assert external_ip_watch["support_state"] == "needs_device_test"
    assert external_ip_watch["handler_id"] == "d04.f30.network.external_ip_watch"
    assert external_ip_watch["role"] == ["owner"]
    assert external_ip_watch["baseline_evidence"][0]["novelty_status"] == "confirmed_absent"
    assert set(external_ip_watch["test_refs"]) == {
        "TG-BOT-SERVER/tests/test_device_store.py::test_external_ip_watch_is_owner_opt_in_persistent_and_queues_changes",
        "TG-BOT-SERVER/tests/test_bot_ui.py::test_external_ip_watch_callbacks_are_owner_only_and_registered",
        "XGENT-WDS/tests/test_wds.py::test_external_ip_response_is_correlated_and_rejects_non_public_ip",
        "XGENT-MCS/tests/test_capabilities.py::test_macos_external_ip_response_correlates_public_result_and_hides_provider_error",
    }
    external_watch_nav = next(row for row in registry["navigation"] if row["callback"] == "cmd:external_ip_watch")
    assert (external_watch_nav["parent"], external_watch_nav["roles"], external_watch_nav["presentation"]) == (
        "network", ["owner"], "network_external_ip_watch",
    )
    implemented_ids.add("D04-F30")
    assert all(
        feature["support_state"] == "planned"
        for feature in features if feature["feature_id"] not in implemented_ids
    )
    assert all(
        feature["handler_id"] is None and feature["test_refs"] == []
        for feature in features if feature["feature_id"] not in implemented_ids
    )
    assert all(
        feature["baseline_evidence"][0]["novelty_status"] == (
                    "confirmed_absent" if feature["feature_id"] in {"D04-F01", "D04-F02", "D04-F03", "D04-F04", "D04-F21", "D04-F22", "D04-F23", "D04-F24", "D04-F25", "D04-F26", "D04-F27", "D04-F28", "D04-F29", "D04-F30", "D05-F13", "D05-F14", "D05-F15", "D05-F16", "D05-F17", "D05-F18", "D05-F19", "D05-F20", "D05-F21", "D05-F22", "D05-F23", "D05-F24", "D05-F25", "D05-F26", "D05-F27", "D05-F28", "D05-F29"} else "needs_novelty_audit"
        )
        for feature in features
    )
    assert all(set(feature["component_min_versions"]) == set(COMPONENTS) for feature in features)
    assert {page["page_id"] for page in registry["navigation"]} >= {
        "home.devices", "home.server", "home.admin", "home.access_request", "home.catalog", "home.about",
    }
    assert next(page for page in registry["navigation"] if page["page_id"] == "home.admin")["roles"] == ["owner"]
    assert next(page for page in registry["navigation"] if page["page_id"] == "home.access_request")["roles"] == ["user"]
    permission_routes = {
        (row["page_id"], row["callback"], tuple(row["roles"]), row["presentation"])
        for row in registry["navigation"] if row["page_id"].startswith("admin.permission_request")
    }
    assert ("admin.permission_requests.row", "admin:access-request:view:*", ("owner",), "permission_request_row") in permission_routes
    assert ("admin.permission_request.approve", "admin:access-request:approve:*", ("owner",), "permission_request_decision") in permission_routes
    assert ("admin.permission_request.reject", "admin:access-request:reject:*", ("owner",), "permission_request_decision") in permission_routes
    assert validate_feature_registry(write_registry(tmp_path, registry)) == []


def test_builder_rejects_duplicate_approved_catalog_rows(tmp_path: Path) -> None:
    spec = importlib.util.find_spec("build_feature_registry")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = (ROOT / SPEC).read_text(encoding="utf-8")
    first_candidate = next(
        line for line in source.splitlines()
        if line.startswith("|") and "-F" in line and line.count("|") == 4
    )
    duplicate_spec = tmp_path / "spec.md"
    duplicate_spec.write_text(source + "\n" + first_candidate + "\n", encoding="utf-8")

    try:
        builder.build_feature_registry(duplicate_spec)
    except ValueError as exc:
        assert "Duplicate catalog table row" in str(exc)
    else:
        raise AssertionError("duplicate approved feature rows must be rejected")


def test_full_runner_registry_gate_fails_closed_for_invalid_registry(tmp_path: Path) -> None:
    runner_spec = importlib.util.spec_from_file_location("xider_run_tests", ROOT / "tools/run_tests.py")
    assert runner_spec is not None and runner_spec.loader is not None
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    invalid_registry = write_registry(tmp_path, "{not-json")

    assert runner.run_registry_gate(invalid_registry) == 1


def test_full_runner_registry_gate_accepts_committed_catalog() -> None:
    runner_spec = importlib.util.spec_from_file_location("xider_run_tests", ROOT / "tools/run_tests.py")
    assert runner_spec is not None and runner_spec.loader is not None
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)

    assert runner.run_registry_gate(ROOT / "docs/features/feature-registry.json") == 0


def test_microphone_selection_registry_tracks_local_evidence_but_requires_device_test():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F15")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f15.microphone.input.selection"
    assert any("test_microphone_devices.py" in ref for ref in feature["test_refs"])
    assert any("test_mic_duration_requires_fresh_catalog" in ref for ref in feature["test_refs"])


def test_microphone_level_registry_tracks_local_evidence_but_requires_device_test():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F16")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f16.microphone.level.meter"
    assert any("test_microphone_level_meter_returns_only_aggregates" in ref for ref in feature["test_refs"])
    assert any("test_microphone_level_is_owner_selected" in ref for ref in feature["test_refs"])


def test_audio_output_catalog_registry_is_owner_scoped_and_requires_device_test():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F19")
    nav = next(row for row in registry["navigation"] if row["callback"] == "audio:outputs")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f19.audio.output.catalog"
    assert feature["role"] == ["owner"]
    assert nav["parent"] == "mic.options" and nav["roles"] == ["owner"]
    assert any("test_audio_output_catalog_is_owner_only" in ref for ref in feature["test_refs"])


def test_audio_output_switch_registry_is_owner_scoped_and_requires_device_acceptance():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F18")
    nav = next(row for row in registry["navigation"] if row["callback"] == "audio:switch")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f18.audio.output.switch"
    assert feature["role"] == ["owner"]
    assert nav["parent"] == "mic.options" and nav["roles"] == ["owner"]
    assert any("test_audio_output_switch_requires_fresh_owner_selection" in ref for ref in feature["test_refs"])


def test_microphone_balance_registry_is_owner_scoped_and_requires_device_acceptance():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F21")
    nav = next(row for row in registry["navigation"] if row["callback"] == "mic:balance")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f21.microphone.stereo.balance"
    assert feature["role"] == ["owner"]
    assert nav["parent"] == "mic.options" and nav["roles"] == ["owner"]
    assert nav["presentation"] == "microphone_balance"
    assert any("test_microphone_balance_is_owner_selected" in ref for ref in feature["test_refs"])


def test_media_history_registry_is_owner_scoped_and_requires_device_acceptance():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F24")
    nav = next(row for row in registry["navigation"] if row["callback"] == "media:history")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f24.media.history.metadata_only"
    assert feature["role"] == ["owner"]
    assert nav["parent"] == "mic.options" and nav["roles"] == ["owner"]
    assert any("test_media_history_is_owner_only_correlated_metadata_and_edits_one_card" in ref for ref in feature["test_refs"])


def test_microphone_roundtrip_registry_is_owner_scoped_and_requires_device_test():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F17")
    nav = next(row for row in registry["navigation"] if row["callback"] == "mic:test")

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f17.microphone.output.roundtrip"
    assert feature["role"] == ["owner"]
    assert nav["parent"] == "mic.options" and nav["roles"] == ["owner"]
    assert any("test_microphone_roundtrip_is_owner_selected" in ref for ref in feature["test_refs"])


def test_microphone_preview_registry_is_owner_scoped_and_requires_device_acceptance():
    registry = build_feature_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "D02-F20")
    nav = next((row for row in registry["navigation"] if row["callback"] == "mic:preview"), None)

    assert feature["support_state"] == "needs_device_test"
    assert feature["handler_id"] == "d02.f20.microphone.preview_before_delivery"
    assert feature["role"] == ["owner"]
    assert nav is not None and nav["parent"] == "mic.options" and nav["roles"] == ["owner"]
    assert nav["presentation"] == "microphone_preview"
    assert any("test_microphone_preview_is_owner_selected" in ref for ref in feature["test_refs"])
