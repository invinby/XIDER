"""Contract checks for the signed, source-free macOS bootstrap path."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_macos_bootstrap_never_prompts_for_vps_password():
    source = (ROOT / "deploy" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "ssh_args=(-o BatchMode=yes" in source
    assert "-o PasswordAuthentication=no" in source
    assert "-o KbdInteractiveAuthentication=no" in source
    assert "введи пароль VPS при запросе" not in source


def test_signed_macos_bootstrap_stages_only_signed_runtime_bundle():
    source = (ROOT / "deploy" / "bootstrap.sh").read_text(encoding="utf-8")
    release_start = source.index('if [[ -n "$RELEASE_TAG" ]]; then\n  mkdir -p "$stage/new/XGENT-MCS"')
    release_end = source.index('\nelse\n  cp -R "$downloaded/." "$stage/new/"', release_start)
    assert "mkdir -p \"$stage/new/XGENT-MCS\"" in source[release_start:release_end]
    assert 'PYTHONPATH="$downloaded/XGENT-MCS"' in source
    assert 'download_verified_release_asset(Path(manifest), Path(bundle), "XGENT-MCS-macos-bundle.zip"' in source
    assert 'cp "$stage/macos-bundle/.env.example" "$new_agent/.env.example"' in source
    assert 'mv "$stage/macos-bundle/$app" "$new_agent/$app"' in source
    assert 'Unexpected plaintext source or helper script in signed macOS install; refusing activation.' in source
    assert 'Подписанные Agent, Guard Keeper и Safari/Siri подготовлены без Python-исходников.' in source


def test_signed_macos_runtime_is_verified_before_stopping_current_services():
    source = (ROOT / "deploy" / "bootstrap.sh").read_text(encoding="utf-8")
    assert source.index("download_verified_release_asset(") < source.index("stop_loaded_job com.xider.siri")
    assert source.index('codesign --verify --deep --strict') < source.index("stop_loaded_job com.xider.siri")
    assert source.index("Unexpected plaintext source or helper script in signed macOS install") < source.index("stop_loaded_job com.xider.siri")
    assert "Критично: recovery" not in source


def test_safari_siri_supervise_the_next_layer_and_guardian_supervises_agent():
    layers = (ROOT / "XGENT-MCS" / "xider_layers.py").read_text(encoding="utf-8")
    guardian = (ROOT / "XGENT-MCS" / "xider_guardian.py").read_text(encoding="utf-8")
    assert '"child_label": "com.xider.guardian"' in layers
    assert '"child_label": "com.xider.safari"' in layers
    assert '"siri": {' in layers and '"safari": {' in layers
    assert "self.start_agent()" in guardian
    assert "guardian_restarted_for_agent" in layers


def test_release_runtime_uses_compiled_guardian_and_layer_tools():
    guardian = (ROOT / "XGENT-MCS" / "start_guardian.sh").read_text(encoding="utf-8")
    safari = (ROOT / "XGENT-MCS" / "start_safari.sh").read_text(encoding="utf-8")
    siri = (ROOT / "XGENT-MCS" / "start_siri.sh").read_text(encoding="utf-8")
    for source in (safari, siri):
        assert ".xider-signed-release" in source
        assert "source fallback is disabled" in source.lower()
    assert "XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper" in guardian
    assert "XIDER-Layers.app/Contents/MacOS/XIDER-Layers" in safari
    assert "XIDER-Layers.app/Contents/MacOS/XIDER-Layers" in siri
    assert "--owner-stop-agent" in guardian
