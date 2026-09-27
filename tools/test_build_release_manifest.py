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


def test_manifest_rejects_bad_tag_or_missing_asset(tmp_path):
    with pytest.raises(ValueError):
        build("main", tmp_path)
    with pytest.raises(FileNotFoundError):
        build("v4.0.0", tmp_path)
