"""Тесты DeviceStore: запись на диск только при изменениях, rename/remove."""

import json
from types import SimpleNamespace

import pytest

import device_store
from device_store import DeviceStore


def test_hardware_inventory_snapshots_compare_only_allowlisted_nonserial_fields(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    earlier = store.save_hardware_inventory_snapshot("device-1", {
        "manufacturer": "Example", "model": "Board A", "cpu": "CPU Model",
        "architecture": "x64", "memory_gib": 8, "graphics": ["GPU One"],
        "serial_number": "MUST_NOT_PERSIST",
    }, now=100)
    later = store.save_hardware_inventory_snapshot("device-1", {
        "manufacturer": "Example", "model": "Board A", "cpu": "CPU Model",
        "architecture": "x64", "memory_gib": 16, "graphics": ["GPU One", "GPU Two"],
    }, now=200)

    comparison = store.compare_hardware_inventory_snapshots("device-1")
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")

    assert earlier["snapshot_id"] != later["snapshot_id"]
    assert comparison["before"]["snapshot_id"] == earlier["snapshot_id"]
    assert comparison["after"]["snapshot_id"] == later["snapshot_id"]
    assert comparison["changes"] == [
        {"field": "graphics", "before": ["GPU One"], "after": ["GPU One", "GPU Two"]},
        {"field": "memory_gib", "before": 8, "after": 16},
    ]
    assert "MUST_NOT_PERSIST" not in persisted


def test_owner_can_store_multiple_normalized_tags_per_device(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)

    assert store.set_device_tags(10, 10, "device-1", [" Test ", "дом", "test", "qa-lab"])
    assert store.get_device_tags(10, 10, "device-1") == ["Test", "дом", "qa-lab"]


def test_manual_device_relations_are_owner_only_symmetric_and_pruned_when_device_is_removed(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("main-pc", {"name": "Main"})
    store.upsert("test-mac", {"name": "Test"})

    assert store.set_device_relation(10, 10, "main-pc", "test-mac", True) is True
    assert store.get_device_relations(10, 10, "main-pc") == ["test-mac"]
    assert store.get_device_relations(10, 10, "test-mac") == ["main-pc"]
    assert store.set_device_relation(99, 10, "main-pc", "test-mac", False) is False
    assert store.get_device_relations(99, 10, "main-pc") is None

    assert store.remove("test-mac") is True
    assert store.get_device_relations(10, 10, "main-pc") == []
    assert store.get_device_relations(10, 10, "test-mac") == []


@pytest.mark.parametrize(
    ("left", "right", "linked"),
    [("main-pc", "main-pc", True), ("main-pc", "missing", True), ("main-pc", "test-mac", 1)],
)
def test_manual_device_relation_rejects_self_unknown_and_non_boolean_state(tmp_path, monkeypatch, left, right, linked):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("main-pc", {})
    store.upsert("test-mac", {})

    with pytest.raises(ValueError):
        store.set_device_relation(10, 10, left, right, linked)
    assert store.get_device_tags(11, 10, "device-1") is None
    assert store.set_device_tags(10, 10, "device-1", ["work"])
    assert store.get_device_tags(10, 10, "device-1") == ["work"]

    with pytest.raises(ValueError):
        store.set_device_tags(10, 10, "device-1", ["x" * 81])
    with pytest.raises(ValueError):
        store.set_device_tags(10, 10, "device-1", ["x"] * 17)


def test_device_purpose_is_a_single_allowlisted_owner_classification(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)

    assert store.set_device_purpose(10, 10, "device-1", "work") is True
    assert store.get_device_purpose(10, 10, "device-1") == "work"
    assert store.get_device_tags(10, 10, "device-1") == []
    assert store.get_device_purpose(11, 10, "device-1") is None
    assert store.set_device_purpose(11, 10, "device-1", "personal") is False

    with pytest.raises(ValueError, match="purpose"):
        store.set_device_purpose(10, 10, "device-1", "travel")

    assert store.set_device_purpose(10, 10, "device-1", None) is True
    assert store.get_device_purpose(10, 10, "device-1") is None


def test_owner_device_review_date_is_strict_persistent_and_clearable(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)

    assert store.set_device_review_date(10, 10, "device-1", "2027-01-31") is True
    assert store.get_device_review_date(10, 10, "device-1") == "2027-01-31"
    assert store.get_device_review_date(11, 10, "device-1") is None
    assert store.set_device_review_date(11, 10, "device-1", "2027-02-01") is False

    with pytest.raises(ValueError, match="date"):
        store.set_device_review_date(10, 10, "device-1", "2027-02-30")
    with pytest.raises(ValueError, match="date"):
        store.set_device_review_date(10, 10, "device-1", "2027-2-3")

    assert store.set_device_review_date(10, 10, "device-1", "") is True
    assert store.get_device_review_date(10, 10, "device-1") is None


def test_device_maintenance_is_owner_only_expires_and_excludes_only_selected_aggregate(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("maintained", {"name": "Maintained", "os": "Windows", "last_seen": 100})
    store.upsert("included", {"name": "Included", "os": "Windows", "last_seen": 100})

    assert store.set_device_maintenance(10, 10, "maintained", "1970-01-02", exclude_inactivity=True) is True
    assert store.get_device_maintenance(10, 10, "maintained", now=100) == {
        "until_date": "1970-01-02", "exclude_inactivity": True, "active": True,
    }
    assert store.get_device_maintenance(11, 10, "maintained", now=100) is None
    assert store.set_device_maintenance(11, 10, "maintained", "1970-01-02") is False
    assert store.capture_inactivity_snapshot(now=100)["counts"]["windows"]["under_7_days"] == 1
    assert store.capture_inactivity_snapshot(now=200_000)["counts"]["windows"]["under_7_days"] == 2
    assert store.get_device_maintenance(10, 10, "maintained", now=200_000)["active"] is False
    assert store.set_device_maintenance(10, 10, "maintained", "", exclude_inactivity=True) is True
    assert store.get_device_maintenance(10, 10, "maintained", now=100) is None


def test_owner_inventory_template_copies_only_safe_metadata_to_blank_device(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("source", {"name": "Lab PC", "os": "Windows"})
    store.upsert("new-device", {"name": "New PC", "os": "Windows"})
    store.set_device_tags(10, 10, "source", ["lab", "loaner"])
    store.set_device_purpose(10, 10, "source", "work")
    store.define_typed_field(10, 10, "asset_type", "enum", choices=["laptop", "desktop"])
    store.set_typed_field(10, 10, "source", "asset_type", "desktop")

    template = store.create_device_inventory_template(10, 10, "Lab workstation", "source", now=100)

    assert template is not None
    assert template["name"] == "Lab workstation"
    assert template["tags"] == ["lab", "loaner"]
    assert template["purpose"] == "work"
    assert template["typed_values"] == {"asset_type": "desktop"}
    template["tags"].append("mutated-return-value")
    assert store.list_device_inventory_templates(10, 10)[0]["tags"] == ["lab", "loaner"]

    assert store.apply_device_inventory_template(10, 10, template["template_id"], "new-device") is True
    assert store.get_device_tags(10, 10, "new-device") == ["lab", "loaner"]
    assert store.get_device_purpose(10, 10, "new-device") == "work"
    assert store.typed_fields(10, 10, "new-device") == {"asset_type": "desktop"}
    assert store.get("new-device")["os"] == "Windows"


def test_inventory_template_is_owner_only_and_never_overwrites_existing_metadata(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("source", {"name": "Lab PC"})
    store.upsert("target", {"name": "Existing PC"})
    template = store.create_device_inventory_template(10, 10, "Blank profile", "source", now=100)

    assert store.create_device_inventory_template(11, 10, "Nope", "source", now=101) is None
    assert store.list_device_inventory_templates(11, 10) is None
    assert store.apply_device_inventory_template(11, 10, template["template_id"], "target") is False

    store.set_device_tags(10, 10, "target", ["already-classified"])
    with pytest.raises(ValueError, match="empty inventory metadata"):
        store.apply_device_inventory_template(10, 10, template["template_id"], "target")
    assert store.get_device_tags(10, 10, "target") == ["already-classified"]


def test_inventory_template_listing_ignores_malformed_purpose_in_persisted_row(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("source", {"name": "Source"})
    store.create_device_inventory_template(10, 10, "Good", "source", now=100)
    store._catalog["inventory_templates"]["c" * 32] = {
        "template_id": "c" * 32, "owner_id": 10, "name": "Malformed", "tags": [],
        "purpose": ["not", "hashable"], "typed_values": {}, "created_at": 99,
    }

    templates = store.list_device_inventory_templates(10, 10)

    assert [row["name"] for row in templates] == ["Good"]


def test_likely_duplicate_detection_requires_same_normalized_hostname_and_platform(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("device-a", {"name": "Office PC", "hostname": "  BUILD-07.Example. ", "os": "Windows 11"})
    store.upsert("device-b", {"name": "Copied install", "hostname": "build-07.example", "os": "Windows 10"})
    store.upsert("device-c", {"name": "Different platform", "hostname": "build-07.example", "os": "macOS 15"})
    store.upsert("device-d", {"name": "Same label, unknown host", "os": "Windows 11"})
    store.upsert("device-e", {"name": "Same label, unknown host", "os": "Windows 11"})

    pairs = store.find_likely_duplicate_device_pairs(10, 10)

    assert pairs == [("device-a", "device-b")]
    assert store.find_likely_duplicate_device_pairs(11, 10) is None


def test_owner_private_device_note_is_local_bounded_and_not_exposed_to_other_actors(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    note = "  Keep   spare charger in the blue case.  "

    assert store.set_private_device_note(10, 10, "device-1", note)
    assert store.get_private_device_note(10, 10, "device-1") == "Keep spare charger in the blue case."
    assert store.get_private_device_note(11, 10, "device-1") is None
    assert store.set_private_device_note(10, 10, "device-1", "")
    assert store.get_private_device_note(10, 10, "device-1") == ""

    with pytest.raises(ValueError):
        store.set_private_device_note(10, 10, "device-1", "x" * 2001)
    with pytest.raises(ValueError):
        store.set_private_device_note(10, 10, "device-1", "bad\x00note")


def test_owner_manual_device_place_label_is_text_not_gps_and_owner_scoped(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)

    assert store.set_device_place_label(10, 10, "device-1", "  Office   · 3rd floor ")
    assert store.get_device_place_label(10, 10, "device-1") == "Office · 3rd floor"
    assert store.get_device_place_label(11, 10, "device-1") is None
    assert store.set_device_place_label(10, 10, "device-1", "")
    assert store.get_device_place_label(10, 10, "device-1") == ""

    with pytest.raises(ValueError, match="text label"):
        store.set_device_place_label(10, 10, "device-1", "41.3111, 69.2797")
    with pytest.raises(ValueError):
        store.set_device_place_label(10, 10, "device-1", "x" * 101)


def test_owner_device_collections_are_persistent_named_groups_not_expiring_snapshots(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("device-1", {"name": "Desk PC"})
    store.upsert("device-2", {"name": "Travel Mac"})

    created = store.create_device_collection(
        10, 10, "  Lab   fleet  ", ["device-1", "device-2", "device-1"], now=100,
    )
    restored = DeviceStore().list_device_collections(actor_id=10, owner_id=10)

    assert created == restored[0]
    assert created["name"] == "Lab fleet"
    assert created["device_ids"] == ["device-1", "device-2"]
    assert created["created_at"] == created["updated_at"] == 100
    assert "expires_at" not in created
    assert store.list_device_collections(actor_id=11, owner_id=10) == []


def test_device_collection_updates_are_owner_only_unique_and_keep_removed_ids_visible(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("device-1", {"name": "Desk PC"})
    store.upsert("device-2", {"name": "Travel Mac"})
    lab = store.create_device_collection(10, 10, "Lab", ["device-1", "device-2"], now=100)
    backup = store.create_device_collection(10, 10, "Backup", ["device-2"], now=101)

    with pytest.raises(ValueError, match="already exists"):
        store.create_device_collection(10, 10, " lab ", ["device-2"], now=102)
    with pytest.raises(ValueError, match="unknown"):
        store.create_device_collection(10, 10, "Unknown", ["missing-device"], now=102)
    with pytest.raises(ValueError, match="already exists"):
        store.update_device_collection(10, 10, lab["collection_id"], name="backup", now=103)

    assert store.update_device_collection(11, 10, lab["collection_id"], name="Forbidden", now=104) is None
    assert store.get_device_collection(11, 10, lab["collection_id"]) is None
    assert store.update_device_collection(10, 10, lab["collection_id"], name="Studio", device_ids=["device-2"], now=105)["name"] == "Studio"
    store.remove("device-2")
    current = store.get_device_collection(10, 10, lab["collection_id"])
    assert current["device_ids"] == []
    assert current["stale_device_ids"] == ["device-2"]
    assert store.delete_device_collection(11, 10, backup["collection_id"]) is False
    assert store.delete_device_collection(10, 10, backup["collection_id"]) is True


def test_owner_saved_device_filters_are_durable_normalized_and_private(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    saved = store.create_saved_device_filter(
        10, 10, "  Macs   online ", "os", "macOS", now=100,
    )
    restored = DeviceStore().list_saved_device_filters(actor_id=10, owner_id=10)

    assert saved == restored[0]
    assert saved["name"] == "Macs online"
    assert saved["filter_type"] == "os"
    assert saved["query"] == "macOS"
    assert "expires_at" not in saved
    assert store.list_saved_device_filters(actor_id=11, owner_id=10) == []
    assert store.get_saved_device_filter(11, 10, saved["filter_id"]) is None


def test_saved_device_filter_rejects_duplicate_names_and_unknown_filter_types(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    saved = store.create_saved_device_filter(10, 10, "Macs", "os", "macOS", now=100)

    with pytest.raises(ValueError, match="already exists"):
        store.create_saved_device_filter(10, 10, " macs ", "os", "Windows", now=101)
    with pytest.raises(ValueError, match="filter type"):
        store.create_saved_device_filter(10, 10, "Bad", "command", "anything", now=101)
    with pytest.raises(ValueError, match="query"):
        store.create_saved_device_filter(10, 10, "Bad query", "os", "\n", now=101)

    assert store.delete_saved_device_filter(10, 10, saved["filter_id"])
    assert store.get_saved_device_filter(10, 10, saved["filter_id"]) is None


def test_owner_device_catalog_sort_preference_is_persistent_and_allowlisted(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)

    assert store.get_device_catalog_sort(10, 10) == "name"
    assert store.set_device_catalog_sort(10, 10, "last_seen") is True
    assert DeviceStore().get_device_catalog_sort(10, 10) == "last_seen"
    assert store.set_device_catalog_sort(11, 10, "version") is False
    assert store.get_device_catalog_sort(11, 10) is None
    with pytest.raises(ValueError, match="sort"):
        store.set_device_catalog_sort(10, 10, "secret")


def test_owner_device_catalog_custom_order_persists_prunes_stale_and_appends_new_devices(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    for device_id in ("a", "b", "c"):
        store.upsert(device_id, {"name": device_id})

    assert store.set_device_catalog_sort(10, 10, "custom") is True
    assert store.set_device_catalog_order(10, 10, ["c", "a", "b"]) is True
    assert store.get_device_catalog_order(10, 10, ["a", "b", "c"]) == ["c", "a", "b"]
    assert DeviceStore().get_device_catalog_order(10, 10, ["a", "b", "c"]) == ["c", "a", "b"]

    store.remove("a")
    store.upsert("d", {"name": "d"})
    assert store.get_device_catalog_order(10, 10, ["b", "c", "d"]) == ["c", "b", "d"]
    assert store.set_device_catalog_order(11, 10, ["b", "c"]) is False
    assert store.get_device_catalog_order(11, 10, ["b", "c"]) is None
    with pytest.raises(ValueError, match="order"):
        store.set_device_catalog_order(10, 10, ["b", "b"])


def test_owner_pinned_device_list_is_persistent_ordered_and_separate_from_action_favorites(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("device-1", {"name": "Desk PC", "favorites": ["screenshot"]})
    store.upsert("device-2", {"name": "Travel Mac"})

    assert store.pin_device(10, 10, "device-1") is True
    assert store.pin_device(10, 10, "device-2") is True
    assert store.pin_device(10, 10, "device-1") is True
    assert store.list_pinned_devices(actor_id=10, owner_id=10) == ["device-1", "device-2"]
    assert DeviceStore().list_pinned_devices(actor_id=10, owner_id=10) == ["device-1", "device-2"]
    assert store.get_favorites("device-1") == ["screenshot"]
    assert store.list_pinned_devices(actor_id=11, owner_id=10) == []
    assert store.pin_device(11, 10, "device-2") is False

    assert store.unpin_device(10, 10, "device-1") is True
    assert store.list_pinned_devices(actor_id=10, owner_id=10) == ["device-2"]


def test_pinned_devices_reject_unknown_ids_and_filter_removed_devices(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("device-1", {"name": "Desk PC"})

    assert store.pin_device(10, 10, "missing") is False
    assert store.pin_device(10, 10, "device-1") is True
    store.remove("device-1")
    assert store.list_pinned_devices(actor_id=10, owner_id=10) == []


def test_removing_a_pinned_device_frees_capacity_for_another_device(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    for index in range(21):
        device_id = f"device-{index}"
        store.upsert(device_id, {"name": device_id})

    assert all(store.pin_device(10, 10, f"device-{index}") for index in range(20))
    assert store.pin_device(10, 10, "device-20") is False
    store.remove("device-0")

    assert len(store.list_pinned_devices(actor_id=10, owner_id=10)) == 19
    assert store.pin_device(10, 10, "device-20") is True
    assert len(store.list_pinned_devices(actor_id=10, owner_id=10)) == 20


def test_pinned_ids_fit_telegram_callback_byte_limit(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    valid_id = "x" * 42
    too_long_id = "x" * 43
    assert len("catalog:pinned:toggle:".encode("utf-8")) + len(valid_id.encode("utf-8")) == 64
    store.upsert(valid_id, {"name": "Valid boundary"})
    store.upsert(too_long_id, {"name": "Too long"})

    assert store.pin_device(10, 10, valid_id) is True
    assert store.pin_device(10, 10, too_long_id) is False


def test_pinning_prunes_malformed_persisted_ids_without_crashing(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("device-1", {"name": "Existing"})
    store.upsert("device-2", {"name": "New"})
    store._catalog["pinned_devices"]["10"] = [["malformed"], "device-1", "x" * 43]

    assert store.pin_device(10, 10, "device-2") is True
    assert store.list_pinned_devices(actor_id=10, owner_id=10) == ["device-1", "device-2"]


def test_smart_counter_snapshots_are_bounded_allowlisted_and_compared_by_disk_label(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_smart_trend_snapshot("device-1", [
        {"label": "Model A [SSD]", "counters": {"temperature_c": 38, "wear_percent": 7,
         "power_on_hours": 1200}},
    ], now=100)
    store.save_smart_trend_snapshot("device-1", [
        {"label": "Model A [SSD]", "counters": {"temperature_c": 40, "wear_percent": 8,
         "power_on_hours": 1210}},
        {"label": "Model B [HDD]", "counters": {"read_errors_total": 2}},
    ], now=200)

    comparison = store.compare_smart_trend_snapshots("device-1")
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")

    assert comparison["elapsed_seconds"] == 100
    assert comparison["changes"] == [
        {"label": "Model A [SSD]", "counter": "power_on_hours", "before": 1200.0,
         "after": 1210.0, "delta": 10.0},
        {"label": "Model A [SSD]", "counter": "temperature_c", "before": 38.0,
         "after": 40.0, "delta": 2.0},
        {"label": "Model A [SSD]", "counter": "wear_percent", "before": 7.0,
         "after": 8.0, "delta": 1.0},
        {"label": "Model B [HDD]", "counter": "read_errors_total", "before": None,
         "after": 2.0, "delta": None},
    ]
    assert "MUST_NOT_PERSIST" not in persisted and "serial_number" not in persisted


def test_smart_counter_snapshot_rejects_invalid_or_unbounded_values(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="SMART"):
        store.save_smart_trend_snapshot("device-1", [
            {"label": "disk", "counters": {"unknown_counter": 4}},
        ], now=100)
    with pytest.raises(ValueError, match="SMART"):
        store.save_smart_trend_snapshot("device-1", [
            {"label": "disk", "counters": {"temperature_c": float("nan")}},
        ], now=100)


def test_driver_snapshots_are_bounded_redacted_and_diffed_by_provider_and_name(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    before = store.save_driver_inventory_snapshot("device-1", [
        {"device_name": "Display Adapter", "provider": "Vendor A", "version": "1.0", "driver_date": "2026-01-01",
         "hardware_id": "MUST_NOT_PERSIST"},
    ], now=100)
    after = store.save_driver_inventory_snapshot("device-1", [
        {"device_name": "Display Adapter", "provider": "Vendor A", "version": "2.0", "driver_date": "2026-06-01"},
        {"device_name": "Audio Device", "provider": "Vendor B", "version": "3.1", "driver_date": None},
    ], now=200)

    comparison = store.compare_driver_inventory_snapshots("device-1")
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")

    assert before["snapshot_id"] != after["snapshot_id"]
    assert comparison["changes"] == [
        {"kind": "added", "device_name": "Audio Device", "provider": "Vendor B", "before": None,
         "after": {"version": "3.1", "driver_date": None}},
        {"kind": "changed", "device_name": "Display Adapter", "provider": "Vendor A",
         "before": {"version": "1.0", "driver_date": "2026-01-01"},
         "after": {"version": "2.0", "driver_date": "2026-06-01"}},
    ]
    assert "MUST_NOT_PERSIST" not in persisted and "hardware_id" not in persisted


def test_installed_app_snapshots_are_allowlisted_bounded_and_diffed(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_installed_app_snapshot("device-1", [
        {"name": "App A", "publisher": "Vendor", "version": "1.0", "install_path": "SECRET"},
        {"name": "App B", "publisher": None, "version": "2"},
    ], now=100)
    store.save_installed_app_snapshot("device-1", [
        {"name": "App A", "publisher": "Vendor", "version": "1.1"},
        {"name": "App C", "publisher": "Vendor C", "version": "1"},
    ], now=200)
    comparison = store.compare_installed_app_snapshots("device-1")
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    assert {item["kind"] for item in comparison["changes"]} == {"changed", "added", "removed"}
    assert "SECRET" not in persisted and "install_path" not in persisted


def test_startup_snapshots_keep_only_names_and_fixed_scopes(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_startup_snapshot("device-1", [{"name": "Updater", "scope": "user_run", "command": "SECRET"}], now=100)
    store.save_startup_snapshot("device-1", [{"name": "Updater", "scope": "user_run"},
                                               {"name": "Audio", "scope": "machine_run"}], now=200)
    comparison = store.compare_startup_snapshots("device-1")
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    assert comparison["changes"] == [{"kind": "added", "name": "Audio", "scope": "machine_run"}]
    assert "SECRET" not in persisted and "command" not in persisted


def test_service_snapshots_keep_only_public_labels_and_diff_fixed_state_fields(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_service_snapshot("device-1", [{"name": "ExampleSvc", "state": "Stopped", "start_type": "Manual",
                                               "binary_path": "SECRET"}], now=100)
    store.save_service_snapshot("device-1", [{"name": "ExampleSvc", "state": "Running", "start_type": "Auto"},
                                              {"name": "NewSvc", "state": "Stopped", "start_type": "Disabled"}], now=200)
    comparison = store.compare_service_snapshots("device-1")
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    assert {item["kind"] for item in comparison["changes"]} == {"changed", "added"}
    assert "SECRET" not in persisted and "binary_path" not in persisted


def test_network_traffic_samples_compute_only_fresh_monotonic_deltas(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    interface_id = "bc60aa9b07ccca8059ab"
    first = store.record_network_traffic_sample(
        "device-1", interface_id, "Wi-Fi", sent_bytes=1200, received_bytes=3400, sampled_at=100,
    )
    assert first["state"] == "baseline"
    second = store.record_network_traffic_sample(
        "device-1", interface_id, "Wi-Fi", sent_bytes=1300, received_bytes=3900, sampled_at=105,
    )
    assert second == {
        "state": "delta", "interface_id": interface_id, "label": "Wi-Fi",
        "sent_delta": 100, "received_delta": 500, "elapsed_seconds": 5,
        "sampled_at": 105,
    }
    reset = store.record_network_traffic_sample(
        "device-1", interface_id, "Wi-Fi", sent_bytes=10, received_bytes=20, sampled_at=110,
    )
    assert reset["state"] == "counter_reset"


def test_network_traffic_sample_lookup_returns_only_redacted_adapter_identity(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.record_network_traffic_sample(
        "device-1", "a" * 20, "Wi-Fi", sent_bytes=100, received_bytes=200, sampled_at=1000,
    )
    assert store.get_network_traffic_sample("device-1", "a" * 20) == {
        "interface_id": "a" * 20, "label": "Wi-Fi", "sampled_at": 1000,
    }
    assert store.get_network_traffic_sample("device-1", "b" * 20) is None
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    assert "IP address" not in persisted and "mac_address" not in persisted


def test_network_traffic_alert_tracks_fresh_usage_and_latches_one_owner_warning(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    schedule_id, interface_id = "a" * 32, "b" * 20
    store.configure_network_traffic_alert(
        "device-1", schedule_id=schedule_id, interface_id=interface_id, label="Wi-Fi",
        interval_seconds=900, threshold_bytes=100_000_000, enabled=True,
    )
    baseline = store.record_network_traffic_alert_sample(
        "device-1", schedule_id=schedule_id, interface_id=interface_id,
        sent_bytes=10, received_bytes=20, sampled_at=1_700_000_900, now=1_700_000_900,
    )
    assert baseline == {"state": "baseline", "alert": None, "bytes_used": 0}

    crossed = store.record_network_traffic_alert_sample(
        "device-1", schedule_id=schedule_id, interface_id=interface_id,
        sent_bytes=40_000_010, received_bytes=60_000_020, sampled_at=1_700_001_800, now=1_700_001_800,
    )
    assert crossed == {
        "state": "alert", "bytes_used": 100_000_000,
        "alert": {"threshold_bytes": 100_000_000, "observed_bytes": 100_000_000,
                  "label": "Wi-Fi", "sampled_at": 1_700_001_800},
    }
    again = store.record_network_traffic_alert_sample(
        "device-1", schedule_id=schedule_id, interface_id=interface_id,
        sent_bytes=40_000_020, received_bytes=60_000_030, sampled_at=1_700_002_700, now=1_700_002_700,
    )
    assert again == {"state": "latched", "alert": None, "bytes_used": 100_000_020}

    stale = store.record_network_traffic_alert_sample(
        "device-1", schedule_id=schedule_id, interface_id=interface_id,
        sent_bytes=1 << 40, received_bytes=1 << 40, sampled_at=1_700_002_000, now=1_700_002_800,
    )
    assert stale["state"] == "unknown"
    disabled = store.configure_network_traffic_alert(
        "device-1", schedule_id="c" * 32, interface_id=interface_id, label="Wi-Fi",
        interval_seconds=0, threshold_bytes=100_000_000, enabled=False,
    )
    assert disabled["enabled"] is False


def test_upsert_writes_only_on_change(tmp_path, monkeypatch):
    f = tmp_path / "dev.json"
    monkeypatch.setattr(DeviceStore, "FILE", f, raising=False)
    store = DeviceStore()

    store.upsert("a1", {"name": "Alpha", "os": "test"})
    assert json.loads(f.read_text(encoding="utf-8"))["a1"]["name"] == "Alpha"

    # Heartbeat без изменений — файл НЕ перезаписывается
    mtime_before = f.stat().st_mtime_ns
    store.upsert("a1", {"name": "Alpha", "os": "test"})
    assert f.stat().st_mtime_ns == mtime_before

    # Изменившиеся данные должны оказаться на диске.  Не сравниваем mtime:
    # на Windows его точность зависит от файловой системы и два write могут
    # попасть в один временной тик.
    store.upsert("a1", {"name": "Alpha-2"})
    assert json.loads(f.read_text(encoding="utf-8"))["a1"]["name"] == "Alpha-2"


def test_rename_and_remove(tmp_path, monkeypatch):
    f = tmp_path / "dev.json"
    monkeypatch.setattr(DeviceStore, "FILE", f, raising=False)
    store = DeviceStore()
    store.upsert("b2", {"name": "Old"})

    assert store.rename("b2", "New") is True
    assert store.get("b2")["name"] == "New"
    assert json.loads(f.read_text(encoding="utf-8"))["b2"]["name"] == "New"

    assert store.rename("missing", "X") is False

    assert store.remove("b2") is True
    assert store.get("b2") is None
    assert json.loads(f.read_text(encoding="utf-8")) == {}
    assert store.remove("b2") is False


def test_toggle_favorites(tmp_path, monkeypatch):
    f = tmp_path / "dev.json"
    monkeypatch.setattr(DeviceStore, "FILE", f, raising=False)
    store = DeviceStore()
    store.upsert("c3", {"name": "X"})

    assert store.get_favorites("c3") == []
    assert store.toggle_favorite("c3", "screenshot") is True
    assert store.get_favorites("c3") == ["screenshot"]
    assert store.toggle_favorite("c3", "screenshot") is False
    assert store.get_favorites("c3") == []
    assert store.toggle_favorite("missing", "screenshot") is False


def _catalog_store(tmp_path, monkeypatch):
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    return DeviceStore()


def test_shell_runbook_templates_are_owner_only_persistent_and_separate_from_execution_plans(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    commands = ["printf first", "printf second"]

    saved = store.create_shell_runbook_template(10, 10, "  Проверка   сети ", commands, now=100)

    assert saved["name"] == "Проверка сети"
    assert saved["commands"] == commands
    assert saved["created_at"] == saved["updated_at"] == 100
    assert store.list_shell_runbook_templates(11, 10) is None
    assert store.get_shell_runbook_template(11, 10, saved["template_id"]) is None
    assert store.list_shell_runbook_templates(10, 10) == [saved]

    reloaded = DeviceStore()
    assert reloaded.get_shell_runbook_template(10, 10, saved["template_id"]) == saved
    assert not any("printf first" in str(row) for row in reloaded._catalog.get("shell_history", []))


def test_shell_runbook_template_rename_and_delete_change_only_template_library(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    saved = store.create_shell_runbook_template(
        10, 10, "Connectivity check", ["printf one", "printf two"], now=200,
    )

    renamed = store.rename_shell_runbook_template(
        10, 10, saved["template_id"], "Connectivity v2", now=210,
    )

    assert renamed["template_id"] == saved["template_id"]
    assert renamed["created_at"] == 200
    assert renamed["updated_at"] == 210
    assert renamed["name"] == "Connectivity v2"
    assert store.delete_shell_runbook_template(10, 10, saved["template_id"])
    assert store.list_shell_runbook_templates(10, 10) == []


@pytest.mark.parametrize("commands", [
    [], ["one"], ["one", ""], ["one", "  "], ["one", "two\nthree"], ["one", "two\x00three"],
    ["x" * 1500, "y" * 1500], ["x" * 2000, "y" * 1001], [str(i) for i in range(9)],
])
def test_shell_runbook_template_rejects_invalid_or_unbounded_commands(tmp_path, monkeypatch, commands):
    store = _catalog_store(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        store.create_shell_runbook_template(10, 10, "Invalid", commands, now=300)


def test_shell_runbook_template_rejects_duplicate_names_and_stale_rename_time(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    saved = store.create_shell_runbook_template(
        10, 10, "Connectivity", ["printf one", "printf two"], now=400,
    )
    with pytest.raises(ValueError):
        store.create_shell_runbook_template(
            10, 10, " connectivity ", ["printf x", "printf y"], now=401,
        )
    with pytest.raises(ValueError):
        store.rename_shell_runbook_template(10, 10, saved["template_id"], "Renamed", now=399)


def test_owner_search_synonyms_persist_and_can_be_removed(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)

    added = store.add_feature_search_synonym(
        "B01-F03", "  мой   накопитель ", actor_id=100, owner_id=100,
    )
    restored = DeviceStore().get_feature_search_synonyms(owner_id=100)

    assert added == ["мой накопитель"]
    assert restored == {"B01-F03": ["мой накопитель"]}
    assert DeviceStore().remove_feature_search_synonym(
        "B01-F03", "мой накопитель", actor_id=100, owner_id=100,
    ) is True
    assert DeviceStore().get_feature_search_synonyms(owner_id=100) == {}


def test_owner_search_synonyms_reject_non_owner_collisions_and_unbounded_input(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.add_feature_search_synonym("B01-F03", "моё устройство", actor_id=100, owner_id=100)

    with pytest.raises(PermissionError):
        store.add_feature_search_synonym("B01-F03", "чужая запись", actor_id=200, owner_id=100)
    with pytest.raises(ValueError, match="already assigned"):
        store.add_feature_search_synonym("B01-F04", "МОЁ УСТРОЙСТВО", actor_id=100, owner_id=100)
    with pytest.raises(ValueError, match="1-64"):
        store.add_feature_search_synonym("B01-F04", "x" * 65, actor_id=100, owner_id=100)

    assert store.get_feature_search_synonyms(owner_id=100) == {"B01-F03": ["моё устройство"]}


def test_system_summary_history_is_allowlisted_bounded_and_expires(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    sample = {
        "platform": "Windows 11", "cpu_percent": 12.5, "ram_percent": 48.0,
        "disk_percent": 61.0, "hostname": "MUST_NOT_PERSIST", "mac": "MUST_NOT_PERSIST",
    }
    first = store.save_system_summary_snapshot("device-1", sample, captured_at=1_000_000, now=1_000_000)
    for index in range(51):
        store.save_system_summary_snapshot(
            "device-1", {**sample, "cpu_percent": float(index)},
            captured_at=1_000_001 + index, now=1_000_001 + index,
        )

    rows = store.list_system_summary_snapshots("device-1", limit=50, now=1_000_051)
    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")

    assert len(rows) == 50
    assert first["snapshot_id"] not in {row["snapshot_id"] for row in rows}
    assert rows[0]["summary"]["cpu_percent"] == 50.0
    assert "MUST_NOT_PERSIST" not in persisted
    assert len(store.list_system_summary_snapshots(
        "device-1", limit=50, now=1_000_051 + store.SYSTEM_SUMMARY_TTL_SECONDS + 1,
    )) == 0


def test_system_summary_comparison_uses_two_newest_snapshots_and_only_allowlisted_fields(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_system_summary_snapshot(
        "device-1", {"platform": "Windows", "cpu_percent": 10, "ram_percent": 40, "disk_percent": 60},
        captured_at=1_000_000, now=1_000_000,
    )
    store.save_system_summary_snapshot(
        "device-1", {"platform": "Windows", "cpu_percent": 20, "ram_percent": 40, "disk_percent": 55},
        captured_at=1_000_100, now=1_000_100,
    )

    comparison = store.compare_system_summary_snapshots("device-1", now=1_000_100)

    assert comparison["before"]["captured_at"] == 1_000_000
    assert comparison["after"]["captured_at"] == 1_000_100
    assert comparison["changes"] == [
        {"field": "cpu_percent", "before": 10.0, "after": 20.0},
        {"field": "disk_percent", "before": 60.0, "after": 55.0},
    ]


def test_system_summary_comparison_requires_two_snapshots(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_system_summary_snapshot(
        "device-1", {"platform": "macOS", "cpu_percent": 10}, captured_at=1_000_000, now=1_000_000,
    )
    with pytest.raises(ValueError, match="two system summary snapshots"):
        store.compare_system_summary_snapshots("device-1", now=1_000_000)


def test_system_summary_schedule_requires_allowlisted_interval_and_schedule_identity(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    assert store.get_system_summary_schedule("device-1") == {
        "interval_seconds": 0, "schedule_id": None, "next_sample_at": None,
    }
    configured = store.save_system_summary_schedule(
        "device-1", interval_seconds=900, schedule_id="a" * 32, next_sample_at=1_900, now=1_000,
    )
    assert configured["interval_seconds"] == 900
    assert store.save_scheduled_system_summary_snapshot(
        "device-1", schedule_id="a" * 32,
        summary={"platform": "Windows", "cpu_percent": 10, "ram_percent": 20, "disk_percent": 30},
        captured_at=1_500, now=1_500,
    ) is not None
    assert store.save_scheduled_system_summary_snapshot(
        "device-1", schedule_id="b" * 32,
        summary={"platform": "Windows", "cpu_percent": 10, "ram_percent": 20, "disk_percent": 30},
        captured_at=1_600, now=1_600,
    ) is None
    with pytest.raises(ValueError):
        store.save_system_summary_schedule(
            "device-1", interval_seconds=60, schedule_id="a" * 32, next_sample_at=1_960, now=1_000,
        )


def test_system_summary_schedule_disable_invalidates_late_samples(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_system_summary_schedule("device-1", interval_seconds=900, schedule_id="a" * 32,
                                       next_sample_at=1_900, now=1_000)
    store.save_system_summary_schedule("device-1", interval_seconds=0, schedule_id="b" * 32,
                                       next_sample_at=None, now=1_100)
    assert store.save_scheduled_system_summary_snapshot(
        "device-1", schedule_id="a" * 32,
        summary={"platform": "Windows", "cpu_percent": 10}, captured_at=1_200, now=1_200,
    ) is None


def test_scheduled_system_summary_aggregates_only_fresh_scheduled_samples_by_period(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_system_summary_schedule("device-1", interval_seconds=900, schedule_id="a" * 32,
                                       next_sample_at=10_900, now=10_000)
    for stamp, cpu, ram, disk in (
        (6_200, 10, 20, 30),       # outside the requested 1-hour window
        (8_200, 20, None, 50),
        (9_100, 40, 60, 70),
    ):
        store.save_scheduled_system_summary_snapshot(
            "device-1", schedule_id="a" * 32,
            summary={"platform": "Windows", "cpu_percent": cpu, "ram_percent": ram, "disk_percent": disk},
            captured_at=stamp, now=10_000,
        )
    # An owner-requested one-shot snapshot is not part of scheduled telemetry.
    store.save_system_summary_snapshot(
        "device-1", {"platform": "Windows", "cpu_percent": 99, "ram_percent": 99, "disk_percent": 99},
        captured_at=9_500, now=10_000,
    )

    report = store.aggregate_scheduled_system_summary("device-1", period_seconds=3_600, now=10_000)

    assert report == {
        "period_seconds": 3_600,
        "start_at": 6_400,
        "end_at": 10_000,
        "sample_count": 2,
        "metrics": {
            "cpu_percent": {"count": 2, "min": 20.0, "mean": 30.0, "max": 40.0},
            "ram_percent": {"count": 1, "min": 60.0, "mean": 60.0, "max": 60.0},
            "disk_percent": {"count": 2, "min": 50.0, "mean": 60.0, "max": 70.0},
        },
    }


def test_scheduled_system_summary_rejects_invalid_period_and_unknown_when_empty(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unsupported system summary period"):
        store.aggregate_scheduled_system_summary("device-1", period_seconds=12, now=10_000)
    with pytest.raises(ValueError, match="no fresh scheduled system summaries"):
        store.aggregate_scheduled_system_summary("device-1", period_seconds=3_600, now=10_000)


def test_system_threshold_alert_requires_two_consecutive_samples_and_rearms_below_hysteresis(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.save_system_summary_schedule("device-1", interval_seconds=900, schedule_id="a" * 32,
                                       next_sample_at=1_900, now=1_000)
    store.set_system_summary_threshold("device-1", "cpu_percent", 80)
    sample = {"platform": "Windows", "cpu_percent": 85, "ram_percent": 40, "disk_percent": 50}
    assert store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary=sample, now=1_100,
    ) == []
    alerts = store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary={**sample, "cpu_percent": 82}, now=2_000,
    )
    assert alerts == [{"metric": "cpu_percent", "threshold": 80.0, "observed": 82.0}]
    assert store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary={**sample, "cpu_percent": 90}, now=3_000,
    ) == []
    assert store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary={**sample, "cpu_percent": 74}, now=4_000,
    ) == []
    assert store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary={**sample, "cpu_percent": 81}, now=5_000,
    ) == []
    assert store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary={**sample, "cpu_percent": 82}, now=6_000,
    ) == [{"metric": "cpu_percent", "threshold": 80.0, "observed": 82.0}]


def test_system_thresholds_are_owner_selected_bounded_and_disabled_schedule_never_alerts(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.set_system_summary_threshold("device-1", "cpu_percent", 80)
    with pytest.raises(ValueError):
        store.set_system_summary_threshold("device-1", "hostname", 80)
    with pytest.raises(ValueError):
        store.set_system_summary_threshold("device-1", "cpu_percent", 800)
    sample = {"platform": "macOS", "cpu_percent": 99, "ram_percent": 99, "disk_percent": 99}
    assert store.evaluate_system_summary_thresholds(
        "device-1", schedule_id="a" * 32, summary=sample, now=1_000,
    ) == []


def test_system_summary_snapshot_lookup_is_device_scoped_and_allowlisted(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    record = store.save_system_summary_snapshot(
        "device-1", {"platform": "Windows", "cpu_percent": 25, "hostname": "DO_NOT_EXPORT"},
        captured_at=1_000_000, now=1_000_000,
    )

    selected = store.get_system_summary_snapshot(
        "device-1", record["snapshot_id"], now=1_000_000,
    )

    assert selected["summary"] == {"platform": "Windows", "cpu_percent": 25.0,
                                   "ram_percent": None, "disk_percent": None}
    assert store.get_system_summary_snapshot("device-2", record["snapshot_id"], now=1_000_000) is None
    assert store.get_system_summary_snapshot("device-1", "x" * 32, now=1_000_000) is None


def test_selected_device_snapshot_expires_and_reports_stale_ids(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac", "os": "macOS", "last_seen": 100})
    store.upsert("win-b", {"name": "PC", "os": "Windows", "last_seen": 100})

    saved = store.save_selected_snapshot(
        100, 100, "test bench", ["mac-a", "win-b"], now=100, ttl_seconds=60
    )
    assert store.save_selected_snapshot(
        200, 100, "not owner", ["mac-a"], now=100
    ) is None
    store.remove("win-b")

    current = store.get_selected_snapshot(
        saved["snapshot_id"], actor_id=100, owner_id=100, now=120
    )
    assert current["device_ids"] == ["mac-a"]
    assert current["stale_device_ids"] == ["win-b"]
    assert store.get_selected_snapshot(
        saved["snapshot_id"], actor_id=200, owner_id=100, now=120
    ) is None
    assert store.get_selected_snapshot(
        saved["snapshot_id"], actor_id=100, owner_id=100, now=160
    ) is None


def test_selected_snapshot_list_is_owner_only_and_omits_expired_rows(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac"})
    live = store.save_selected_snapshot(100, 100, "current", ["mac-a"], now=100, ttl_seconds=60)
    store.save_selected_snapshot(100, 100, "expired", ["mac-a"], now=20, ttl_seconds=60)

    rows = store.list_selected_snapshots(actor_id=100, owner_id=100, now=120)

    assert [row["snapshot_id"] for row in rows] == [live["snapshot_id"]]
    assert store.list_selected_snapshots(actor_id=200, owner_id=100, now=120) == []


def test_selected_snapshot_delete_is_owner_only_and_id_scoped(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac"})
    first = store.save_selected_snapshot(100, 100, "first", ["mac-a"], now=100)
    second = store.save_selected_snapshot(100, 100, "second", ["mac-a"], now=100)

    assert store.delete_selected_snapshot(200, 100, first["snapshot_id"]) is False
    assert store.delete_selected_snapshot(100, 100, "../catalog.json") is False
    assert store.get_selected_snapshot(first["snapshot_id"], actor_id=100, owner_id=100, now=101) is not None
    assert store.delete_selected_snapshot(100, 100, first["snapshot_id"]) is True
    assert store.get_selected_snapshot(first["snapshot_id"], actor_id=100, owner_id=100, now=101) is None
    assert store.get_selected_snapshot(second["snapshot_id"], actor_id=100, owner_id=100, now=101) is not None


def test_device_archive_is_owner_only_reversible_and_does_not_remove_device(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("old-mac", {"name": "Old Mac", "os": "macOS"})

    assert store.archive_device(10, 10, "old-mac", now=123) is True
    assert store.get_device_archive(10, 10, "old-mac") == {"archived_at": 123}
    assert store.list_archived_devices(10, 10) == [("old-mac", {"name": "Old Mac", "os": "macOS"})]
    assert store.get("old-mac") == {"name": "Old Mac", "os": "macOS"}
    assert store.archive_device(11, 10, "old-mac", now=124) is False
    assert store.restore_device(11, 10, "old-mac") is False
    assert store.restore_device(10, 10, "missing") is False
    assert store.restore_device(10, 10, "old-mac") is True
    assert store.get_device_archive(10, 10, "old-mac") is None
    assert store.list_archived_devices(10, 10) == []


def test_owner_typed_inventory_fields_validate_enum_date_and_boolean(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac"})
    assert store.define_typed_field(100, 100, "purpose", "enum", choices=["personal", "test"])
    assert store.define_typed_field(100, 100, "reviewed_on", "date")
    assert store.define_typed_field(100, 100, "is_spare", "boolean")

    assert store.set_typed_field(100, 100, "mac-a", "purpose", "test")
    assert store.set_typed_field(100, 100, "mac-a", "reviewed_on", "2026-10-15")
    assert store.set_typed_field(100, 100, "mac-a", "is_spare", True)
    assert store.typed_fields(100, 100, "mac-a") == {
        "purpose": "test", "reviewed_on": "2026-10-15", "is_spare": True,
    }
    assert store.typed_fields(200, 100, "mac-a") is None
    assert store.define_typed_field(200, 100, "private", "boolean") is False
    with pytest.raises(ValueError):
        store.set_typed_field(100, 100, "mac-a", "purpose", "unknown")
    with pytest.raises(ValueError):
        store.set_typed_field(100, 100, "mac-a", "reviewed_on", "yesterday")


def test_typed_field_schemas_are_owner_only_and_return_detached_data(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.define_typed_field(100, 100, "purpose", "enum", choices=["personal", "test"])

    schemas = store.typed_field_schemas(100, 100)

    assert schemas == {"purpose": {"type": "enum", "choices": ["personal", "test"]}}
    schemas["purpose"]["choices"].append("mutated")
    assert store.typed_field_schemas(100, 100) == {
        "purpose": {"type": "enum", "choices": ["personal", "test"]}
    }
    assert store.typed_field_schemas(200, 100) is None


def test_inactivity_history_is_aggregate_and_returns_platform_deltas(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac", "os": "macOS", "last_seen": 100})
    store.upsert("win-b", {"name": "PC", "os": "Windows", "last_seen": 100})

    first = store.capture_inactivity_snapshot(now=100)
    second = store.capture_inactivity_snapshot(now=100 + 8 * 86400)

    delta = store.compare_inactivity_snapshots(first["snapshot_id"], second["snapshot_id"])
    assert delta["macos"]["over_30_days"] == 0
    assert delta["windows"]["7_to_30_days"] == 1
    assert delta["windows"]["under_7_days"] == -1
    assert "device_ids" not in first


def test_inactivity_history_listing_is_newest_first_and_aggregate_only(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac", "os": "macOS", "last_seen": 100})
    first = store.capture_inactivity_snapshot(now=100)
    second = store.capture_inactivity_snapshot(now=200)

    rows = store.list_inactivity_snapshots()

    assert [row["snapshot_id"] for row in rows] == [second["snapshot_id"], first["snapshot_id"]]
    assert rows[0] == {
        "snapshot_id": second["snapshot_id"],
        "captured_at": 200,
        "counts": {"macos": {"under_7_days": 1, "7_to_30_days": 0, "over_30_days": 0, "unknown": 0}},
    }
    assert store.list_inactivity_snapshots(limit=1) == [rows[0]]
    with pytest.raises(ValueError):
        store.list_inactivity_snapshots(limit=0)


def test_inactivity_history_uses_capture_order_for_same_second(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    ids = iter(("f" * 32, "0" * 32))
    monkeypatch.setattr(device_store.uuid, "uuid4", lambda: SimpleNamespace(hex=next(ids)))

    first = store.capture_inactivity_snapshot(now=100)
    second = store.capture_inactivity_snapshot(now=100)

    assert [row["snapshot_id"] for row in store.list_inactivity_snapshots()] == [
        second["snapshot_id"], first["snapshot_id"],
    ]


def test_inactivity_history_skips_malformed_persisted_rows(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    valid = store.capture_inactivity_snapshot(now=100)
    store._catalog["inactivity_snapshots"].extend((
        {"snapshot_id": "bad-time", "captured_at": "later", "counts": {}},
        {"snapshot_id": "bad-counts", "captured_at": 200, "counts": ["not", "a", "mapping"]},
        {"snapshot_id": "negative-count", "captured_at": 300, "counts": {"windows": {"under_7_days": -1}}},
    ))
    store._save_catalog()
    reloaded = DeviceStore()

    rows = reloaded.list_inactivity_snapshots()

    assert [row["snapshot_id"] for row in rows] == [valid["snapshot_id"]]


def test_catalog_store_recovers_wrong_typed_json_collections(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "snapshots": [],
        "field_schemas": "invalid",
        "typed_values": None,
        "inactivity_snapshots": {},
    }), encoding="utf-8")
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", path, raising=False)
    store = DeviceStore()
    store.upsert("mac-a", {"name": "Mac", "os": "macOS", "last_seen": 100})

    snapshot = store.capture_inactivity_snapshot(now=100)

    assert snapshot["counts"]["macos"]["under_7_days"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_navigation_state_restores_back_stack_and_expires(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac"})
    stack = [
        {"page": "main", "cursor": 0},
        {"page": "catalog-area", "area": "B01", "cursor": 2},
    ]

    saved = store.save_navigation_state(
        200, "mac-a", "catalog-area:B01", 2, stack,
        now=100, ttl_seconds=60,
    )
    assert store.get_navigation_state(200, now=120)["stack"] == stack
    assert store.get_navigation_state(200, now=160) is None


def test_navigation_restore_invalidates_removed_selected_device(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.upsert("mac-a", {"name": "Mac"})
    saved = store.save_navigation_state(
        200, "mac-a", "device", 0, [{"page": "device"}], now=100,
    )
    store.remove("mac-a")

    restored = store.get_navigation_state(200, now=101)

    assert saved["user_id"] == 200
    assert restored["selected_device_id"] is None
    assert restored["stale_device"] is True


def test_device_registration_provenance_preserves_origin_and_counts_reregistrations(tmp_path, monkeypatch):
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()

    store.upsert("heartbeat-device", {"name": "Mac"}, registration_event="heartbeat", now=100)
    store.upsert("heartbeat-device", {"online": True}, registration_event="heartbeat", now=110)
    store.upsert("heartbeat-device", {"online": True}, registration_event="re_registration", now=200)
    store.upsert("manual-device", {"name": "PC"}, registration_event="manual_add", now=300)

    heartbeat = store.get("heartbeat-device")
    manual = store.get("manual-device")
    assert heartbeat["registration_origin"] == "heartbeat"
    assert heartbeat["registration_count"] == 2
    assert heartbeat["registered_at"] == 100
    assert heartbeat["last_registered_at"] == 200
    assert heartbeat["last_registration_event"] == "re_registration"
    assert manual["registration_origin"] == "manual_add"
    assert manual["registration_count"] == 1
    assert manual["registered_at"] == 300


def test_device_registration_provenance_rejects_unrecognized_events(tmp_path, monkeypatch):
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()

    with pytest.raises(ValueError, match="registration event"):
        store.upsert("device", {"name": "PC"}, registration_event="shell")


def test_wifi_quality_history_is_owner_only_bounded_and_persists_without_identity(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    sample = {"captured_at": 1_800_000_000, "source": "windows_netsh_signal_percent",
              "metric": "signal_percent", "value": 76}
    assert store.record_wifi_quality_sample(101, 100, "device-1", sample) is None
    assert store.list_wifi_quality_samples(101, 100, "device-1") is None
    for index in range(65):
        store.record_wifi_quality_sample(100, 100, "device-1", {**sample, "captured_at": 1_800_000_000 + index})
    rows = store.list_wifi_quality_samples(100, 100, "device-1")
    assert len(rows) == 60 and rows[0]["captured_at"] == 1_800_000_005
    assert all(set(row) == {"captured_at", "source", "metric", "value"} for row in rows)
    assert DeviceStore().list_wifi_quality_samples(100, 100, "device-1") == rows


def test_wifi_quality_history_rejects_invalid_metrics_and_values(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    valid = {"captured_at": 1_800_000_000, "source": "macos_ipconfig_agrctlrssi",
             "metric": "rssi_dbm", "value": -54}
    assert store.record_wifi_quality_sample(100, 100, "device-1", valid) is not None
    for invalid in (
        {**valid, "value": 10}, {**valid, "metric": "signal_percent"},
        {**valid, "captured_at": -1}, {**valid, "ssid": "private"},
    ):
        with pytest.raises(ValueError):
            store.record_wifi_quality_sample(100, 100, "device-1", invalid)


def test_network_check_sets_are_owner_only_bounded_and_persist(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    endpoints = [
        {"protocol": "tcp", "host": "192.168.1.1", "port": 22},
        {"protocol": "tls", "host": "example.com", "port": 443},
    ]

    saved = store.save_network_check_set(100, 100, "  Home network  ", endpoints, now=123)

    assert saved["name"] == "Home network"
    assert saved["endpoints"] == endpoints
    assert store.list_network_check_sets(101, 100) == []
    assert store.get_network_check_set(101, 100, saved["set_id"]) is None
    restored = DeviceStore().get_network_check_set(100, 100, saved["set_id"])
    assert restored == saved
    restored["endpoints"][0]["host"] = "mutated.example"
    assert store.get_network_check_set(100, 100, saved["set_id"])["endpoints"][0]["host"] == "192.168.1.1"
    assert store.delete_network_check_set(100, 100, saved["set_id"]) is True
    assert store.get_network_check_set(100, 100, saved["set_id"]) is None


def test_network_check_sets_canonicalize_ipv6_and_ignore_corrupt_persisted_rows(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    saved = store.save_network_check_set(100, 100, "IPv6", [
        {"protocol": "tcp", "host": "2001:0db8::1", "port": 443},
    ], now=123)
    assert saved["endpoints"] == [{"protocol": "tcp", "host": "2001:db8::1", "port": 443}]
    store._catalog["network_check_sets"]["100"]["bad-id"] = {
        "owner_id": 100, "name": [], "endpoints": "bad", "created_at": "broken",
    }
    assert store.list_network_check_sets(100, 100) == [saved]


@pytest.mark.parametrize("name,endpoints", [
    ("", [{"protocol": "tcp", "host": "example.com", "port": 443}]),
    ("same", [{"protocol": "udp", "host": "example.com", "port": 443}]),
    ("same", [{"protocol": "tcp", "host": "https://example.com", "port": 443}]),
    ("same", [{"protocol": "tls", "host": "example.com", "port": True}]),
    ("same", [{"protocol": {"invalid": True}, "host": "example.com", "port": 443}]),
    ("same", [{"protocol": "tcp", "host": "example.com", "port": 443, "path": "/"}]),
    ("same", [{"protocol": "tcp", "host": "example.com", "port": 443}] * 6),
    ("same", [{"protocol": "tcp", "host": "example.com", "port": 443}] * 2),
])
def test_network_check_sets_reject_invalid_names_or_targets(tmp_path, monkeypatch, name, endpoints):
    store = _catalog_store(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        store.save_network_check_set(100, 100, name, endpoints, now=123)


def test_external_ip_watch_is_owner_opt_in_persistent_and_queues_changes(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    assert store.set_external_ip_watch(101, 100, "device-1", 900, now=100) is None
    watch = store.set_external_ip_watch(100, 100, "device-1", 900, now=100)
    assert watch == {
        "device_id": "device-1", "owner_id": 100, "interval_seconds": 900,
        "enabled_at": 100, "last_attempt_at": None, "last_checked_at": None, "last_ip": None,
        "last_observed_at": None, "pending_alerts": [],
    }
    first = store.record_external_ip_sample(100, 100, "device-1", "8.8.8.8", now=110)
    same = store.record_external_ip_sample(100, 100, "device-1", "8.8.8.8", now=200)
    changed = store.record_external_ip_sample(100, 100, "device-1", "1.1.1.1", now=300)
    assert first["changed"] is False and same["changed"] is False
    assert changed["changed"] is True
    assert changed["previous_ip"] == "8.8.8.8" and changed["current_ip"] == "1.1.1.1"
    assert len(DeviceStore().list_external_ip_watches(100, 100)) == 1
    pending = store.pending_external_ip_alerts(100, 100, "device-1")
    assert pending == [{
        "alert_id": changed["alert_id"], "previous_ip": "8.8.8.8", "current_ip": "1.1.1.1", "changed_at": 300,
    }]
    assert store.mark_external_ip_alert_delivered(100, 100, "device-1", changed["alert_id"]) is True
    assert store.pending_external_ip_alerts(100, 100, "device-1") == []
    assert store.disable_external_ip_watch(100, 100, "device-1") is True
    assert store.list_external_ip_watches(100, 100) == []


@pytest.mark.parametrize("ip", ["10.0.0.1", "127.0.0.1", "192.168.1.2", "not-an-ip", "2001:db8::1"])
def test_external_ip_watch_rejects_nonpublic_or_invalid_addresses(tmp_path, monkeypatch, ip):
    store = _catalog_store(tmp_path, monkeypatch)
    store.set_external_ip_watch(100, 100, "device-1", 900, now=100)
    with pytest.raises(ValueError):
        store.record_external_ip_sample(100, 100, "device-1", ip, now=200)


def test_external_ip_watch_rejects_unapproved_intervals(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    for interval in (0, 60, 300, 600, 86400, True):
        with pytest.raises(ValueError):
            store.set_external_ip_watch(100, 100, "device-1", interval, now=100)


def test_external_ip_watch_records_failed_attempt_without_faking_success(tmp_path, monkeypatch):
    store = _catalog_store(tmp_path, monkeypatch)
    store.set_external_ip_watch(100, 100, "device-1", 900, now=100)

    assert store.note_external_ip_attempt(101, 100, "device-1", now=200) is False
    assert store.note_external_ip_attempt(100, 100, "device-1", now=200) is True
    row = store.list_external_ip_watches(100, 100)[0]
    assert row["last_attempt_at"] == 200
    assert row["last_checked_at"] is None
    assert row["last_ip"] is None

