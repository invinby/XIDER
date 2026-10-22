import json

import pytest

from device_catalog_import import compare_device_catalogs, parse_device_catalog_import


def test_json_import_reports_new_missing_and_field_conflicts_without_mutating_current():
    payload = b'[{"device_id":"a","name":"renamed","online":true},{"device_id":"c","name":"new"}]'
    current = {
        "a": {"name": "old", "online": False},
        "b": {"name": "kept", "online": True},
    }

    imported = parse_device_catalog_import(payload, "inventory.json")
    result = compare_device_catalogs(imported, current)

    assert result == {
        "new_ids": ["c"],
        "missing_ids": ["b"],
        "unchanged_ids": [],
        "conflicts": [{"device_id": "a", "fields": ["name", "online"]}],
    }
    assert current["a"] == {"name": "old", "online": False}


def test_csv_import_accepts_exported_catalog_and_normalizes_types():
    payload = b'device_id,name,online,last_seen\r\na,Alpha,true,123.5\r\n'

    assert parse_device_catalog_import(payload, "inventory.csv") == [
        {"device_id": "a", "name": "Alpha", "online": True, "last_seen": 123.5}
    ]


@pytest.mark.parametrize("payload", [
    b'[{"device_id":"a","token":"must reject"}]',
    b'[{"device_id":"a"},{"device_id":"a"}]',
    b'[{"device_id":""}]',
    b'[{"device_id":"a","online":"yes"}]',
    b'[{"device_id":"a","last_seen":1e999}]',
])
def test_import_rejects_secret_unknown_duplicate_and_invalid_values(payload):
    with pytest.raises(ValueError):
        parse_device_catalog_import(payload, "inventory.json")


def test_import_rejects_oversize_payload_and_too_many_rows():
    with pytest.raises(ValueError, match="size"):
        parse_device_catalog_import(b" " * (1024 * 1024 + 1), "inventory.json")
    with pytest.raises(ValueError, match="rows"):
        parse_device_catalog_import(
            json.dumps([{"device_id": f"device-{index}"} for index in range(501)]).encode(),
            "inventory.json",
        )
