import hashlib
import json
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


def _obfuscation_report(platform, component_ids):
    return {
        "schema": "xider.obfuscation-report.v1",
        "platform": platform,
        "source_tree": "XGENT-WDS" if platform == "windows" else "XGENT-MCS",
        "state": "applied",
        "tool": {"name": "PyObfus", "version": "0.11.1", "edition": "Community"},
        "configuration": "test cross-file configuration",
        "source_files_processed": 5,
        "names_obfuscated": 42,
        "syntax_verified": True,
        "name_binding_verified": True,
        "execution_performed_by_obfuscator": False,
        "preflight": {"high_risk_findings": 0, "parse_errors": 0},
        "components": [
            {"id": item, "state": "applied", "source_tree": "XGENT-WDS" if platform == "windows" else "XGENT-MCS"}
            for item in component_ids
        ],
    }


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
    members_by_archive = {
        "XGENT-WDS-Windows.zip": {
            "XGENT-WDS.exe": b"windows-agent",
            "xider-obfuscation-report.json": json.dumps(_obfuscation_report(
                "windows", ["windows_agent", "windows_guard_keeper"],
            )).encode(),
        },
        "Guard-Keeper-Windows.zip": {
            "XIDER-Guard-Keeper.exe": b"windows-keeper",
            "xider-obfuscation-report.json": json.dumps(_obfuscation_report(
                "windows", ["windows_agent", "windows_guard_keeper"],
            )).encode(),
        },
        "XGENT-MCS-macos-bundle.zip": {
            "XIDER.app/Contents/MacOS/XGENT-MCS": b"macos-agent",
            "XIDER-Layers.app/Contents/MacOS/XIDER-Layers": b"macos-layers",
            "xider-obfuscation-report.json": json.dumps(_obfuscation_report(
                "macos", ["macos_agent", "macos_guard_keeper", "safari", "siri"],
            )).encode(),
        },
        "Guard-Keeper-macOS.zip": {
            "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper": b"macos-keeper",
            "xider-obfuscation-report.json": json.dumps(_obfuscation_report(
                "macos", ["macos_guard_keeper"],
            )).encode(),
        },
    }
    for name in EXPECTED_ARCHIVES:
        members = override_members if name == override_name else members_by_archive.get(name, {"package.bin": name.encode()})
        with zipfile.ZipFile(directory / name, "w") as package:
            for member_name, content in members.items():
                package.writestr(member_name, content)


def test_manifest_hashes_archives_and_signed_obfuscation_evidence(tmp_path):
    _archives(tmp_path, "XGENT-WDS-Windows.zip", {
        "XGENT-WDS.exe": b"compiled-agent",
        "xider-obfuscation-report.json": json.dumps(_obfuscation_report(
            "windows", ["windows_agent", "windows_guard_keeper"],
        )).encode(),
    })
    archive = tmp_path / "XGENT-WDS-Windows.zip"

    manifest = build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)

    assert manifest["build_kind"] == "artifact_inventory"
    assert manifest["obfuscation"]["state"] == "obfuscated"
    assert manifest["platform_signing"] == {
        "windows": "not_claimed",
        "macos": "unsigned_preview",
        "inventory": "signed_and_verified",
    }
    asset = next(row for row in manifest["assets"] if row["name"] == archive.name)
    assert asset["build_kind"] == "compiled_source_free_package"
    assert asset["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert [row["path"] for row in asset["members"]] == [
        "XGENT-WDS.exe", "xider-obfuscation-report.json",
    ]
    assert asset["members"][0]["sha256"] == hashlib.sha256(b"compiled-agent").hexdigest()
    component = next(row for row in manifest["components"] if row["id"] == "windows_agent")
    assert component["version"] == "v4.3.4"
    assert component["compiler"] == "Nuitka 4.3.1"
    assert component["source_included"] is False
    assert component["obfuscation"]["state"] == "obfuscated"
    assert component["obfuscation"]["tool"] == "PyObfus"
    assert component["obfuscation"]["source_files_processed"] == 5
    assert component["obfuscation"]["report_sha256"] == hashlib.sha256(
        json.dumps(_obfuscation_report("windows", ["windows_agent", "windows_guard_keeper"])).encode()
    ).hexdigest()
    assert component["output_sha256"] == hashlib.sha256(b"compiled-agent").hexdigest()
    assert component["output_verification"] == "sha256_in_signed_member_inventory"


def test_component_inventory_reports_safari_and_siri_as_one_shared_bundle(tmp_path):
    _archives(tmp_path)

    manifest = build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)

    safari = next(row for row in manifest["components"] if row["id"] == "safari")
    siri = next(row for row in manifest["components"] if row["id"] == "siri")
    assert safari["package_asset"] == siri["package_asset"] == "XGENT-MCS-macos-bundle.zip"
    assert safari["entrypoint"] == siri["entrypoint"]
    assert safari["output_sha256"] == siri["output_sha256"] == hashlib.sha256(b"macos-layers").hexdigest()
    assert all(row["obfuscation"]["state"] == "obfuscated" for row in manifest["components"])


def test_preview_inventory_marks_every_missing_macos_component_not_built(tmp_path):
    _archives(tmp_path)
    (tmp_path / "XGENT-MCS-macos-bundle.zip").unlink()
    (tmp_path / "Guard-Keeper-macOS.zip").unlink()

    manifest = build_manifest(
        tmp_path, source_ref="v4.3.17", source_commit=COMMIT, allow_missing_platforms=True,
    )

    missing = [row for row in manifest["components"] if row["platform"] == "macos"]
    assert {row["id"] for row in missing} == {
        "macos_agent", "macos_guard_keeper", "safari", "siri",
    }
    assert all(row["build_mode"] == "not_built" for row in missing)
    assert all(row["package_asset"] is None and row["output_sha256"] is None for row in missing)
    assert manifest["platform_signing"]["macos"] == "source_preview_no_native_package"
    assert manifest["components"][0]["build_mode"] == "nuitka_compiled"


def test_full_build_inventory_still_requires_macos_archives(tmp_path):
    _archives(tmp_path)
    (tmp_path / "XGENT-MCS-macos-bundle.zip").unlink()

    with pytest.raises(ValueError, match="Build archive set mismatch"):
        build_manifest(tmp_path, source_ref="v4.3.17", source_commit=COMMIT)


def test_manifest_rejects_missing_component_entrypoint(tmp_path):
    _archives(tmp_path, "XGENT-WDS-Windows.zip", {
        "unexpected.bin": b"not the agent",
        "xider-obfuscation-report.json": json.dumps(_obfuscation_report(
            "windows", ["windows_agent", "windows_guard_keeper"],
        )).encode(),
    })

    with pytest.raises(ValueError, match="windows_agent entrypoint is missing"):
        build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)


@pytest.mark.parametrize(("field", "value"), [
    ("platform", "macos"),
    ("source_tree", "XGENT-MCS"),
    ("source_files_processed", True),
    ("names_obfuscated", 0),
])
def test_manifest_rejects_mismatched_or_invalid_obfuscation_report(tmp_path, field, value):
    report = _obfuscation_report("windows", ["windows_agent", "windows_guard_keeper"])
    report[field] = value
    _archives(tmp_path, "XGENT-WDS-Windows.zip", {
        "XGENT-WDS.exe": b"compiled-agent",
        "xider-obfuscation-report.json": json.dumps(report).encode(),
    })

    with pytest.raises(ValueError, match="Obfuscation report does not satisfy"):
        build_manifest(tmp_path, source_ref="v4.3.4", source_commit=COMMIT)


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
