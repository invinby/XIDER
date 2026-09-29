import io
import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def guardian_module(monkeypatch):
    monkeypatch.setenv("SHARED_KEY", "test-only-shared-key-guard-0123456789")
    monkeypatch.setenv("MQTT_BROKER", "localhost")
    monkeypatch.setenv("MQTT_PORT", "8883")
    monkeypatch.setenv("MQTT_TLS", "true")
    monkeypatch.setenv("ENCRYPT_PAYLOAD", "true")
    monkeypatch.setenv("MQTT_PREFIX", "xgent/test")
    monkeypatch.setenv("XIDER_UPDATE_BRANCH", "test-repair")
    import xider_guardian

    return xider_guardian


def test_guardian_downloads_missing_allowlisted_agent_and_recreates_env(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name in module.AGENT_FILES:
            content = b"# recovered test file\n"
            archive.writestr(f"XIDER-test/XGENT-MCS/{name}", content)

    def fake_download(_url, destination):
        Path(destination).write_bytes(payload.getvalue())

    monkeypatch.setattr(module.urllib.request, "urlretrieve", fake_download)
    guardian = object.__new__(module.Guardian)

    restored = guardian._restore_missing_agent_files()

    assert set(restored) == set(module.AGENT_FILES) | {".env"}
    assert (tmp_path / "xgent_mcs.py").read_text(encoding="utf-8").startswith("# recovered")
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'SHARED_KEY="test-only-shared-key-guard-0123456789"' in env
    assert "MQTT_BROKER=localhost" not in env
    if os.name != "nt":
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


def test_guardian_rejects_unsafe_update_branch(guardian_module, monkeypatch, tmp_path):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(module, "XIDER_UPDATE_BRANCH", "../main")
    guardian = object.__new__(module.Guardian)

    with pytest.raises(RuntimeError, match="Небезопасное имя ветки"):
        guardian._restore_missing_agent_files()


def test_old_guardian_state_enables_recovery_but_preserves_explicit_stop(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    state_file = tmp_path / "guardian.json"
    state_file.write_text(
        json.dumps({"auto_restart": False, "desired_running": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "STATE_FILE", state_file)

    guardian = module.Guardian()

    assert guardian.state["auto_restart"] is True
    assert guardian.state["desired_running"] is False
    assert guardian.state["state_version"] == 2


def test_worker_autorun_controls_guardian_desired_state(
    guardian_module, monkeypatch, tmp_path
):
    import config

    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "guardian.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

    module.set_guardian_desired_running(False)
    assert json.loads(module.STATE_FILE.read_text(encoding="utf-8"))["desired_running"] is False

    module.set_guardian_desired_running(True)
    assert json.loads(module.STATE_FILE.read_text(encoding="utf-8"))["desired_running"] is True


def test_autorun_toggle_is_separate_from_current_worker_state(
    guardian_module, monkeypatch, tmp_path
):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    module = guardian_module
    module.set_guardian_startup_enabled(False)
    state = json.loads((tmp_path / "guardian.json").read_text(encoding="utf-8"))
    assert state["startup_enabled"] is False
    assert state["desired_running"] is True


def test_new_os_boot_applies_autorun_policy_once(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "guardian.json")
    module.STATE_FILE.write_text(json.dumps({
        "state_version": 2, "auto_restart": True, "desired_running": True,
        "startup_enabled": False, "boot_time": 100,
    }), encoding="utf-8")
    monkeypatch.setattr(module.psutil, "boot_time", lambda: 200)
    monkeypatch.setattr(module.mqtt, "Client", lambda *args, **kwargs: Mock())

    guardian = module.Guardian()

    assert guardian.state["desired_running"] is False
    assert guardian.state["boot_time"] == 200


def test_recovery_toggle_does_not_override_explicit_worker_stop(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "guardian.json")
    guardian = object.__new__(module.Guardian)
    guardian.state = {"state_version": 2, "auto_restart": True, "desired_running": False}
    monkeypatch.setattr(guardian, "status_payload", lambda: {"ok": True})
    monkeypatch.setattr(guardian, "_publish", lambda _payload: None)

    guardian.handle({"command": "auto_restart", "enabled": False})

    assert guardian.state["auto_restart"] is False
    assert guardian.state["desired_running"] is False


def test_recovery_toggle_does_not_override_explicit_worker_stop(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "guardian.json")
    guardian = object.__new__(module.Guardian)
    guardian.state = {"state_version": 2, "auto_restart": True, "desired_running": False}
    monkeypatch.setattr(guardian, "status_payload", lambda: {"ok": True})
    monkeypatch.setattr(guardian, "_publish", lambda _payload: None)

    guardian.handle({"command": "auto_restart", "enabled": False})

    assert guardian.state["auto_restart"] is False
    assert guardian.state["desired_running"] is False
