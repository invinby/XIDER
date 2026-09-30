import json
import sys
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


def test_guardian_fails_closed_when_worker_or_env_is_missing(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    guardian = object.__new__(module.Guardian)

    with pytest.raises(RuntimeError, match="Подписанный recovery-пакет ещё не установлен"):
        guardian._restore_missing_agent_files()

    assert list(tmp_path.iterdir()) == []


def test_guardian_uses_inherited_config_without_recreating_missing_env(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    for name in ("xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py"):
        (tmp_path / name).write_text("# test payload\n", encoding="utf-8")
    guardian = object.__new__(module.Guardian)

    assert guardian._restore_missing_agent_files() == []
    assert not (tmp_path / ".env").exists()


def test_guardian_does_not_recreate_env_when_required_settings_are_unavailable(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    for name in ("xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py"):
        (tmp_path / name).write_text("# test payload\n", encoding="utf-8")
    for key in ("SHARED_KEY", "MQTT_BROKER", "MQTT_PORT", "MQTT_PREFIX", "MQTT_TLS", "ENCRYPT_PAYLOAD"):
        monkeypatch.delenv(key, raising=False)
    guardian = object.__new__(module.Guardian)

    with pytest.raises(RuntimeError, match=r"\.env \(нет параметров:"):
        guardian._restore_missing_agent_files()

    assert not (tmp_path / ".env").exists()


def test_guardian_reports_missing_agent_on_remote_start(guardian_module, monkeypatch, tmp_path):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    guardian = object.__new__(module.Guardian)
    guardian.state = {"desired_running": False}
    replies = []
    monkeypatch.setattr(module, "_save_state", lambda _state: None)
    monkeypatch.setattr(guardian, "agent_pid", lambda: None)
    monkeypatch.setattr(guardian, "_publish", replies.append)

    guardian.handle({"command": "start", "id": "repair-check"})

    assert replies and replies[0]["ok"] is False
    assert replies[0]["id"] == "repair-check"
    assert "Подписанный recovery-пакет ещё не установлен" in replies[0]["text"]
    assert list(tmp_path.iterdir()) == []


def test_guardian_backoff_limits_duplicate_recovery_notifications(guardian_module, monkeypatch):
    module = guardian_module
    guardian = object.__new__(module.Guardian)
    guardian._reset_recovery_backoff()
    published = []
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(guardian, "_publish", published.append)

    guardian._record_recovery_failure(RuntimeError("worker files are missing"))
    assert guardian._next_restart_at == 105.0
    assert guardian._restart_backoff_seconds == 10.0
    assert len(published) == 1

    guardian._record_recovery_failure(RuntimeError("worker files are missing"))
    assert guardian._next_restart_at == 110.0
    assert guardian._restart_backoff_seconds == 20.0
    assert len(published) == 1

    guardian._record_recovery_failure(RuntimeError("signed package is invalid"))
    assert guardian._next_restart_at == 120.0
    assert guardian._restart_backoff_seconds == 40.0
    assert len(published) == 2

    guardian._reset_recovery_backoff()
    assert guardian._next_restart_at == 0.0
    assert guardian._restart_backoff_seconds == 5.0
    assert guardian._last_recovery_error is None


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
