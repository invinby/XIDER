import base64
import copy
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from build_release_manifest import COMPONENTS, build
from provision_release_key import (
    ROOT as REPOSITORY_ROOT,
    create_release_key,
)
from release_signature import release_key_id, verify_manifest_signature


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


def test_manifest_includes_exact_bootstrap_and_quickstart_assets(tmp_path):
    for name in COMPONENTS:
        (tmp_path / name).write_bytes(name.encode())

    manifest = build("v4.0.2", tmp_path)
    entries = {item["name"]: item for item in manifest["assets"]}
    expected = {
        "XIDER-bootstrap-windows.ps1": ("bootstrap", "windows"),
        "XIDER-bootstrap-macos.sh": ("bootstrap", "macos"),
        "XIDER-QUICKSTART.txt": ("quickstart", "all"),
    }
    for name, (component, platform) in expected.items():
        payload = name.encode()
        assert entries[name]["component"] == component
        assert entries[name]["platform"] == platform
        assert entries[name]["size"] == len(payload)
        assert entries[name]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_manifest_rejects_bad_tag_or_missing_asset(tmp_path):
    with pytest.raises(ValueError):
        build("main", tmp_path)
    with pytest.raises(FileNotFoundError):
        build("v4.0.0", tmp_path)


def test_signed_manifest_authenticates_exact_release_inventory(tmp_path):
    for name in COMPONENTS:
        (tmp_path / name).write_bytes(name.encode())
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    manifest = build("v4.5.0", tmp_path, signing_key_pem=private_pem)
    key_id = release_key_id(public_bytes)
    assert verify_manifest_signature(manifest, {key_id: public_bytes}) == key_id

    tampered = copy.deepcopy(manifest)
    tampered["assets"][0]["size"] += 1
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_manifest_signature(tampered, {key_id: public_bytes})

    tampered_quickstart = copy.deepcopy(manifest)
    quickstart = next(item for item in tampered_quickstart["assets"] if item["name"] == "XIDER-QUICKSTART.txt")
    quickstart["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_manifest_signature(tampered_quickstart, {key_id: public_bytes})

    with pytest.raises(ValueError, match="unknown publisher key"):
        verify_manifest_signature(manifest, {})


def test_release_cli_refuses_to_write_unsigned_manifest(tmp_path):
    for name in COMPONENTS:
        (tmp_path / name).write_bytes(name.encode())

    env = os.environ.copy()
    env.pop("XIDER_RELEASE_PRIVATE_KEY_B64", None)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("build_release_manifest.py")),
            "v4.5.1",
            str(tmp_path),
            "--require-signature",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert "XIDER_RELEASE_PRIVATE_KEY_B64 is required" in result.stderr
    assert not (tmp_path / "release-manifest.json").exists()


def test_release_cli_refuses_to_write_manifest_from_unpinned_key(tmp_path):
    for name in COMPONENTS:
        (tmp_path / name).write_bytes(name.encode())
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    env = os.environ.copy()
    env["XIDER_RELEASE_PRIVATE_KEY_B64"] = base64.b64encode(private_pem).decode("ascii")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("build_release_manifest.py")),
            "v4.5.1",
            str(tmp_path),
            "--require-signature",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert "does not match a public key pinned" in result.stderr
    assert not (tmp_path / "release-manifest.json").exists()


def test_release_key_provisioning_writes_private_files_and_only_reports_public_key(tmp_path):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    output_dir = tmp_path / "protected-release-key"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("provision_release_key.py")),
            "--directory",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout
    private_path = output_dir / "release-signing-key.pem"
    private_pem = private_path.read_bytes()
    private_key = load_pem_private_key(private_pem, password=None)
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    assert private_path.exists()
    assert (output_dir / "release-signing-key.b64").read_text(encoding="ascii") == base64.b64encode(private_pem).decode("ascii")
    assert f"public_key_hex={public_raw.hex()}" in output
    assert f"key_id={release_key_id(public_raw)}" in output
    assert private_pem.decode("ascii") not in output


def test_release_key_provisioning_refuses_checkout_and_existing_directory(tmp_path):
    from provision_release_key import _secure_new_directory

    with pytest.raises(ValueError, match="outside the repository"):
        _secure_new_directory(REPOSITORY_ROOT / ".test-release-key")

    existing = tmp_path / "already-there"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to reuse"):
        create_release_key(existing)
    assert marker.read_text(encoding="utf-8") == "preserve"
