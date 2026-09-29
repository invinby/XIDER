import json
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def guardian_module(monkeypatch):
    monkeypatch.setenv("SHARED_KEY", "test-only-windows-guardian-key-012345")
    monkeypatch.setenv("MQTT_BROKER", "localhost")
    monkeypatch.setenv("MQTT_PORT", "8883")
    monkeypatch.setenv("MQTT_TLS", "true")
    monkeypatch.setenv("ENCRYPT_PAYLOAD", "true")
    monkeypatch.setenv("MQTT_PREFIX", "xgent/test")
    import xider_guardian_wds

    return xider_guardian_wds


def _use_temp_state(monkeypatch, module, tmp_path):
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "guardian-windows.json")
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)


def test_new_guardian_install_enables_recovery_by_default(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)

    guardian = module.Guardian()

    assert guardian.state["state_version"] == 2
    assert guardian.state["auto_restart"] is True
    assert guardian.state["desired_running"] is True


def test_legacy_state_migrates_recovery_but_preserves_explicit_stop(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    module.STATE_FILE.write_text(
        json.dumps({"auto_restart": False, "desired_running": False}),
        encoding="utf-8",
    )

    guardian = module.Guardian()

    assert guardian.state["state_version"] == 2
    assert guardian.state["auto_restart"] is True
    assert guardian.state["desired_running"] is False


def test_explicit_recovery_opt_out_in_current_state_is_preserved(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    module.STATE_FILE.write_text(
        json.dumps({"state_version": 2, "auto_restart": False, "desired_running": True}),
        encoding="utf-8",
    )

    guardian = module.Guardian()

    assert guardian.state["auto_restart"] is False


def test_stop_ends_agent_task_before_terminating_worker(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    guardian = object.__new__(module.Guardian)
    guardian.state = {"state_version": 2, "auto_restart": True, "desired_running": True}
    worker = Mock()
    worker.wait.return_value = None
    monkeypatch.setattr(guardian, "agent_process", lambda: worker)
    monkeypatch.setattr(module, "_task_exists", lambda _name: True)
    run = Mock()
    monkeypatch.setattr(module.subprocess, "run", run)

    guardian.stop_agent()

    assert guardian.state["desired_running"] is False
    assert run.call_args.args[0] == ["schtasks.exe", "/End", "/TN", module.AGENT_TASK]
    worker.terminate.assert_called_once_with()

