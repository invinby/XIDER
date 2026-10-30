import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_protected_build_manifest import EXPECTED_ARCHIVES, build_manifest, sign_and_verify
from release_signature import verify_manifest_signature


COMMIT = "a" * 40


def _key():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    from release_signature import release_key_id
    return pem, {release_key_id(public): public}


def _archives(directory, override_name=None, override_members=None):
    for name in EXPECTED_ARCHIVES:
        members = override_members if name == override_name else {"package.bin": name.encode()}
        with zipfile.ZipFile(directory / name, "w") as package:
            for member_name, content in members.items():
                package.writestr(member_name, content)


def test_manifest_hashes_archives_and_each_member_and_states_no_obfuscation(tmp_path):
    _archives(tmp_path, "XGENT-WDS-Windows.zip", {
        "XGENT-WDS.exe": b"compiled-agent", "XIDER-Guard-Keeper.exe": b"compiled-keeper",
    })
    archive = tmp_path / "XGENT-WDS-Windows.zip"

    manifest = build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)

    assert manifest["build_kind"] == "artifact_inventory"
    assert manifest["obfuscation"]["state"] == "not_applied"
    assert manifest["platform_signing"] == {
        "windows": "not_claimed",
        "macos": "unsigned_preview",
        "inventory": "signed_and_verified",
    }
    asset = next(row for row in manifest["assets"] if row["name"] == archive.name)
    assert asset["build_kind"] == "compiled_source_free_package"
    assert asset["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert [row["path"] for row in asset["members"]] == [
        "XGENT-WDS.exe", "XIDER-Guard-Keeper.exe",
    ]
    assert asset["members"][0]["sha256"] == hashlib.sha256(b"compiled-agent").hexdigest()


def test_manifest_must_be_signed_by_a_pinned_release_key(tmp_path):
    _archives(tmp_path)
    manifest = build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)

    pem, _test_keys = _key()
    with pytest.raises(ValueError, match="not pinned"):
        sign_and_verify(manifest, pem)


@pytest.mark.parametrize("source_ref,source_commit", [
    ("main", COMMIT), ("v4.3.4", "abc"),
])
def test_manifest_rejects_non_version_or_unpinned_commit(source_ref, source_commit, tmp_path):
    _archives(tmp_path)
    with pytest.raises(ValueError):
        build_manifest(tmp_path, source_ref=source_ref, source_commit=source_commit)


def test_signed_manifest_detects_tampered_member_hash(tmp_path):
    _archives(tmp_path)
    manifest = build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)
    pem, test_keys = _key()
    signed = sign_and_verify(manifest, pem, trusted_keys=test_keys)
    assert verify_manifest_signature(signed, test_keys)

    tampered = dict(signed)
    tampered["assets"] = [dict(signed["assets"][0])]
    tampered["assets"][0]["members"] = [dict(signed["assets"][0]["members"][0])]
    tampered["assets"][0]["members"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_manifest_signature(tampered, test_keys)


def test_manifest_rejects_unsafe_zip_member(tmp_path):
    _archives(tmp_path, "XIDER-source.zip", {"../outside.txt": b"escape"})
    with pytest.raises(ValueError, match="Unsafe or duplicate"):
        build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)


def test_cli_refuses_unsigned_manifest_when_signing_key_is_missing(tmp_path):
    _archives(tmp_path)
    env = os.environ.copy()
    env.pop("XIDER_RELEASE_PRIVATE_KEY_B64", None)
    script = Path(__file__).with_name("build_protected_build_manifest.py")
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), "--source-ref", "v4.3.4",
         "--source-commit", COMMIT, "--require-signature"],
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode != 0
    assert "XIDER_RELEASE_PRIVATE_KEY_B64 is required" in result.stderr
    assert not (tmp_path / "protected-build-manifest.json").exists()
