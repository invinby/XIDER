import hashlib

import pytest

from build_release_manifest import COMPONENTS, build


def test_manifest_records_exact_package_bytes(tmp_path):
    for name in COMPONENTS:
        (tmp_path / name).write_bytes(name.encode())
    manifest = build("v4.0.0", tmp_path)
    assert manifest["schema"] == 1
    assert manifest["release"] == "v4.0.0"
    assert len(manifest["assets"]) == len(COMPONENTS)
    first = manifest["assets"][0]
    assert first["sha256"] == hashlib.sha256(first["name"].encode()).hexdigest()


def test_manifest_includes_an_immutable_source_bundle(tmp_path):
    for name in COMPONENTS:
        (tmp_path / name).write_bytes(name.encode())

    manifest = build("v4.0.1", tmp_path)
    source = next(item for item in manifest["assets"] if item["name"] == "XIDER-source.zip")

    assert source["component"] == "source"
    assert source["platform"] == "all"
    assert source["size"] == len(b"XIDER-source.zip")
    assert source["sha256"] == hashlib.sha256(b"XIDER-source.zip").hexdigest()


def test_manifest_rejects_bad_tag_or_missing_asset(tmp_path):
    with pytest.raises(ValueError):
        build("main", tmp_path)
    with pytest.raises(FileNotFoundError):
        build("v4.0.0", tmp_path)
