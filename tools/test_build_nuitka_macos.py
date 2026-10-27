import plistlib
from pathlib import Path

import subprocess

import pytest

import build_nuitka_macos as builder
from build_nuitka_macos import (
    _assert_compiled_bundles_are_source_free,
    _command,
    _hidden_imports,
    _prepare_bundle,
    _verify_agent_executable,
    _version,
)


def test_macos_builder_reuses_dynamic_import_inventory_and_stable_version():
    imports = _hidden_imports()

    assert "CoreLocation" in imports
    assert "command_approval_bundle_smoke" in imports
    assert _version().count(".") == 2


def test_nuitka_command_keeps_each_privacy_description_as_one_argument(tmp_path):
    command = _command(
        Path("agent.py"), tmp_path, "XGENT-MCS", version="4.2.0",
        bundle_name="XIDER", bundle_id="com.xider.agent",
        modules=("CoreLocation", "location_adapter"),
        protected_resources={
            "NSLocationUsageDescription": "Explicit one-shot location request.",
            "NSLocationWhenInUseUsageDescription": "Explicit one-shot location request.",
        },
    )

    assert "--mode=app" in command
    assert "--mode=standalone" not in command
    assert "--macos-create-app-bundle" not in command
    assert "--macos-app-name=XIDER" in command
    assert "--macos-app-version=4.2.0" in command
    assert "--include-module=CoreLocation" in command
    assert "--include-module=location_adapter" in command
    assert "--macos-app-protected-resource=NSLocationUsageDescription:Explicit one-shot location request." not in command
    assert "--macos-app-protected-resource=NSLocationWhenInUseUsageDescription:Explicit one-shot location request." in command


def test_prepare_bundle_preserves_executable_name_and_sets_tcc_purpose(tmp_path):
    app = tmp_path / "XIDER.app"
    contents = app / "Contents"
    binary = contents / "MacOS" / "XGENT-MCS"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"compiled-binary")
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleExecutable": "old-name"}, stream)

    _prepare_bundle(
        app, bundle_name="XIDER", bundle_id="com.xider.agent",
        executable="XGENT-MCS", version="4.2.0",
        resources={"NSLocationUsageDescription": "One request at a time."},
    )

    with (contents / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleIdentifier"] == "com.xider.agent"
    assert info["CFBundleExecutable"] == "XGENT-MCS"
    assert info["CFBundleVersion"] == "4.2.0"
    assert info["NSLocationUsageDescription"] == "One request at a time."


def test_agent_smoke_verifier_uses_nuitka_binary_mode(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kwargs: calls.append(command))

    _verify_agent_executable(tmp_path / "XGENT-MCS")

    assert len(calls) == 1
    assert "--nuitka-executable" in calls[0]
    assert "--executable" not in calls[0]


def test_source_scan_checks_the_final_copied_app_bundles(tmp_path):
    build_app = tmp_path / "build" / "XIDER.app"
    final_app = tmp_path / "dist" / "XIDER.app"
    (build_app / "Contents").mkdir(parents=True)
    (final_app / "Contents").mkdir(parents=True)
    (build_app / "Contents" / "stray.py").write_text("source", encoding="utf-8")

    _assert_compiled_bundles_are_source_free(((build_app, final_app),))

    (final_app / "Contents" / "stray.py").write_text("source", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Compiled bundle contains"):
        _assert_compiled_bundles_are_source_free(((build_app, final_app),))
