import json
import threading
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
    import config

    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "guardian.json")
    monkeypatch.setattr(module, "LEGACY_STATE_FILE", tmp_path / "guardian-windows.json")
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)


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


def test_windows_specific_state_file_migrates_to_shared_guardian_state(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    module.LEGACY_STATE_FILE.write_text(
        json.dumps({"state_version": 2, "auto_restart": False, "desired_running": False}),
        encoding="utf-8",
    )

    guardian = module.Guardian()

    assert guardian.state["auto_restart"] is False
    assert guardian.state["desired_running"] is False
    assert module.STATE_FILE.exists()


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


def test_worker_autorun_controls_guardian_desired_state(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)

    module.set_guardian_desired_running(False)
    assert json.loads((tmp_path / "guardian.json").read_text(encoding="utf-8"))["desired_running"] is False

    module.set_guardian_desired_running(True)
    assert json.loads((tmp_path / "guardian.json").read_text(encoding="utf-8"))["desired_running"] is True


def test_concurrent_guardian_state_updates_merge_without_losing_fields(
    guardian_module, monkeypatch, tmp_path
):
    from concurrent.futures import ThreadPoolExecutor
    import config

    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)

    def write_field(key, value):
        for _ in range(30):
            config.update_guardian_state(**{key: value})

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(write_field, "desired_running", False),
            pool.submit(write_field, "auto_restart", False),
            pool.submit(write_field, "startup_enabled", False),
        ]
        for future in futures:
            future.result(timeout=10)

    state = json.loads((tmp_path / "guardian.json").read_text(encoding="utf-8"))
    assert state["desired_running"] is False
    assert state["auto_restart"] is False
    assert state["startup_enabled"] is False
    assert not list(tmp_path.glob("guardian-state.*.tmp"))


def test_guardian_observes_worker_stop_intent(guardian_module, monkeypatch, tmp_path):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    module.STATE_FILE.write_text(
        json.dumps({"state_version": 2, "desired_running": False, "startup_enabled": True}),
        encoding="utf-8",
    )
    guardian = object.__new__(module.Guardian)
    guardian.state = {"desired_running": True, "startup_enabled": True}

    guardian._refresh_worker_intent()

    assert guardian.state["desired_running"] is False
    assert guardian.state["startup_enabled"] is True


def test_explicit_worker_stop_persists_guardian_intent(guardian_module, monkeypatch):
    import xgent_wds as wds

    saved = []
    monkeypatch.setattr(wds, "set_guardian_desired_running", saved.append)
    client = object.__new__(wds.XgentClient)
    client._running = threading.Event()
    client._running.set()
    client.on_stop_requested = None

    client.request_stop()

    assert saved == [False]
    assert not client._running.is_set()


def test_local_stop_cli_persists_intent_without_starting_guardian(guardian_module, monkeypatch):
    module = guardian_module
    saved = []
    monkeypatch.setattr(module, "set_guardian_desired_running", saved.append)
    monkeypatch.setattr(
        module.Guardian,
        "run",
        lambda _self: pytest.fail("stop intent must not start the Guardian MQTT loop"),
    )

    assert module.main(["--set-desired-running", "stop"]) == 0
    assert saved == [False]


def test_local_start_cli_persists_intent_without_starting_guardian(guardian_module, monkeypatch):
    module = guardian_module
    saved = []
    monkeypatch.setattr(module, "set_guardian_desired_running", saved.append)
    monkeypatch.setattr(
        module.Guardian,
        "run",
        lambda _self: pytest.fail("start intent must not start a second Guardian MQTT loop"),
    )

    assert module.main(["--set-desired-running", "start"]) == 0
    assert saved == [True]


def test_stop_agent_batch_persists_intent_before_ending_worker(guardian_module):
    batch = Path(guardian_module.__file__).resolve().parent / "stop_agent.bat"
    source = batch.read_text(encoding="utf-8")
    intent = source.index("--set-desired-running stop")
    stop_task = source.index('schtasks /End /TN "XIDER Agent"')
    failure_guard = source.index("if errorlevel 1", intent)

    assert intent < failure_guard < stop_task


def test_start_agent_batch_persists_intent_before_starting_worker(guardian_module):
    batch = Path(guardian_module.__file__).resolve().parent / "start_agent.bat"
    source = batch.read_text(encoding="utf-8")
    intent = source.index("--set-desired-running start")
    start_task = source.index('schtasks /Run /TN "XIDER Agent"')
    failure_guard = source.index("if errorlevel 1", intent)

    assert intent < failure_guard < start_task


def test_autorun_toggle_is_separate_from_current_worker_state(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    module.set_guardian_startup_enabled(False)
    state = json.loads((tmp_path / "guardian.json").read_text(encoding="utf-8"))
    assert state["startup_enabled"] is False
    assert state["desired_running"] is True


def test_new_os_boot_applies_autorun_policy_once(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
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
    _use_temp_state(monkeypatch, module, tmp_path)
    guardian = object.__new__(module.Guardian)
    guardian.state = {"state_version": 2, "auto_restart": True, "desired_running": False}
    monkeypatch.setattr(guardian, "status_payload", lambda: {"ok": True})
    monkeypatch.setattr(guardian, "_publish", lambda _payload: None)

    guardian.handle({"command": "auto_restart", "enabled": False})

    assert guardian.state["auto_restart"] is False
    assert guardian.state["desired_running"] is False


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
