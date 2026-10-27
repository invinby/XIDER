"""Build contracts evaluated through the actual specs' Analysis arguments."""
import os
from pathlib import Path
import runpy
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = {"command_module_loader", "command_signing", "command_replay", "shell_execution"}


@pytest.mark.parametrize("component", ["XGENT-WDS", "XGENT-MCS"])
def test_nuitka_builders_reuse_the_command_gate_inventory(component):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        builder = runpy.run_path(str(ROOT / "tools" / (
            "build_nuitka_windows.py" if component == "XGENT-WDS" else "build_nuitka_macos.py"
        )))
        hidden_imports = set(builder["_hidden_imports"]())
    finally:
        sys.path.pop(0)

    assert MODULES <= hidden_imports
    assert "cryptography.hazmat.primitives.asymmetric.ed25519" in hidden_imports
    assert "media_history" in hidden_imports
    assert "media_request_control" in hidden_imports
    assert "dns_settings" in hidden_imports
    assert "dns_comparison" in hidden_imports
    assert "command_approval_bundle_smoke" in hidden_imports


def test_ci_pins_nuitka_and_checks_compiled_agent_artifacts():
    workflow = (ROOT / ".github" / "workflows" / "build-agents.yml").read_text(encoding="utf-8")
    build_lines = [line.strip().lower() for line in workflow.splitlines()
                   if "nuitka==" in line.lower()]
    assert len(build_lines) >= 2
    assert all("nuitka==4.3.1" in line for line in build_lines)
    assert "python tools/build_nuitka_windows.py" in workflow
    assert "python tools/build_nuitka_macos.py" in workflow
    assert workflow.count("--nuitka-executable") >= 1
    assert workflow.count("verify_command_approval_bundle.py") >= 2
    verifier = runpy.run_path(str(ROOT / "tools" / "verify_command_approval_bundle.py"))
    assert MODULES | {"cryptography.hazmat.primitives.asymmetric.ed25519"} <= verifier["REQUIRED"]
    assert workflow.count("--public-only-zip") >= 4


def test_windows_bundle_verifier_requires_geolocation_runtime_modules():
    verifier = runpy.run_path(str(ROOT / "tools" / "verify_command_approval_bundle.py"))

    assert {
        "winrt.windows.devices.geolocation",
        "winrt.windows.security.authorization.appcapabilityaccess",
    } <= verifier["REQUIRED"]


def test_public_only_archive_accepts_example_but_rejects_private_files(tmp_path):
    verifier = runpy.run_path(str(ROOT / "tools" / "verify_command_approval_bundle.py"))
    acceptable = tmp_path / "public.zip"
    with zipfile.ZipFile(acceptable, "w") as archive:
        archive.writestr("XGENT/.env.example", "MQTT_TLS=true\n")
        archive.writestr("XGENT/README.md", "public docs\n")
        archive.writestr("tests/test_fixture.py", 'KEY = "-----BEGIN PRIVATE KEY-----"\n')
    verifier["verify_public_only_zip"](acceptable)

    for member, content in (
        ("XGENT/.env", "SHARED_KEY=secret\n"),
        ("deploy/command-signing-private.pem", "not copied\n"),
        ("keys/unexpected.pem", "-----BEGIN PRIVATE KEY-----\nsecret\n"),
        ("keys/command-signing-private.txt", "-----BEGIN PRIVATE KEY-----\nsecret\n"),
        ("keys/id_ed25519", "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n"),
        ("keys/opaque.txt", "-----BEGIN PRIVATE KEY-----\nsecret\n"),
        ("config/release-signing-key.b64", "cHJpdmF0ZS1rZXk=\n"),
    ):
        archive_path = tmp_path / (member.rsplit("/", 1)[-1].replace(".", "_") + ".zip")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(member, content)
        with pytest.raises(ValueError, match="Private"):
            verifier["verify_public_only_zip"](archive_path)

    boundary_archive = tmp_path / "boundary.zip"
    with zipfile.ZipFile(boundary_archive, "w") as archive:
        archive.writestr("data/blob.txt", "x" * (1024 * 1024 - 8)
                         + "\n-----BEGIN PRIVATE KEY-----\nmaterial\n")
    with pytest.raises(ValueError, match="Private"):
        verifier["verify_public_only_zip"](boundary_archive)
