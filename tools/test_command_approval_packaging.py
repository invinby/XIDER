"""Build contracts evaluated through the actual specs' Analysis arguments."""
import os
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = {"command_module_loader", "command_signing", "command_replay"}


@pytest.mark.parametrize("component", ["XGENT-WDS", "XGENT-MCS"])
def test_specs_feed_command_gate_and_probe_to_pyinstaller(component, monkeypatch):
    captured = {}
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = lambda _name: ([], [], [])
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setenv("XIDER_MACOS_SIGNING_IDENTITY", "Apple Development: Fixture (TEAM)")
    def analysis(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
    runpy.run_path(str(ROOT / component / f"{component}.spec"), init_globals={
        "SPECPATH": str(ROOT / component), "Analysis": analysis,
        "PYZ": lambda *_a: None, "EXE": lambda *_a, **_kw: None,
        "BUNDLE": lambda *_a, **_kw: None,
    })
    assert MODULES <= set(captured["hiddenimports"])
    assert "cryptography.hazmat.primitives.asymmetric.ed25519" in captured["hiddenimports"]
    assert captured["runtime_hooks"] == [str(ROOT / "tools" / "command_approval_bundle_smoke.py")]


def test_ci_pins_pyinstaller_and_checks_real_agent_archives():
    workflow = (ROOT / ".github" / "workflows" / "build-agents.yml").read_text(encoding="utf-8")
    install_lines = [line.strip().lower() for line in workflow.splitlines()
                     if "pip install" in line.lower() and "pyinstaller" in line.lower()]
    assert len(install_lines) >= 2
    assert all("pyinstaller==6.22.3" in line for line in install_lines)
    assert workflow.count("verify_command_approval_bundle.py") >= 2
    verifier = runpy.run_path(str(ROOT / "tools" / "verify_command_approval_bundle.py"))
    assert MODULES | {"cryptography.hazmat.primitives.asymmetric.ed25519"} <= verifier["REQUIRED"]
    assert workflow.count("--public-only-zip") >= 4


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
