import json
import threading
import time
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


def test_runtime_install_dir_uses_windows_executable_directory_for_frozen_build(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    executable = tmp_path / "XIDER-Guard-Keeper.exe"
    executable.write_bytes(b"test")
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(module.sys, "executable", str(executable))

    assert module._runtime_install_dir() == tmp_path.resolve()


def test_runtime_install_dir_uses_nuitka_containing_dir(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    compiled_dir = tmp_path / "installed"
    compiled_dir.mkdir()
    monkeypatch.setattr(module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(module, "__compiled__", type("Compiled", (), {"containing_dir": str(compiled_dir)})(), raising=False)

    assert module._runtime_install_dir() == compiled_dir.resolve()


def test_frozen_update_request_is_bound_to_operation_release_and_component(guardian_module):
    module = guardian_module
    request = {
        "id": "a" * 32,
        "release_tag": "v4.2.0",
        "manifest_digest": "b" * 64,
        "component": "windows_agent",
    }

    assert module.validate_frozen_update_request(request) == (
        "a" * 32, "v4.2.0", "b" * 64,
    )
    with pytest.raises(ValueError):
        module.validate_frozen_update_request({**request, "id": "..\\escape"})
    with pytest.raises(ValueError):
        module.validate_frozen_update_request({**request, "component": "mac_agent"})


def _frozen_update_guardian(module, monkeypatch, tmp_path, health_results):
    guardian = object.__new__(module.Guardian)
    guardian._update_lock = threading.Lock()
    guardian._active_update_id = "a" * 32
    guardian.lock = threading.RLock()
    guardian.state = {"desired_running": True}
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path / "install")
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "config" / "guardian.json")
    (tmp_path / "install").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "install" / "XGENT-WDS.exe").write_bytes(b"old-agent")
    progress = []
    results = []
    monkeypatch.setattr(guardian, "_publish_update_progress", lambda *args: progress.append(args[-1]))
    monkeypatch.setattr(guardian, "_publish", results.append)
    monkeypatch.setattr(guardian, "_stop_agent_for_update", lambda: None)
    monkeypatch.setattr(guardian, "start_agent", lambda: 321)
    health_iter = iter(health_results)
    monkeypatch.setattr(guardian, "_wait_for_agent_health", lambda *args: next(health_iter))

    def download(_manifest_path, asset_path, asset_name, *, release_tag, expected_manifest_digest):
        assert asset_name == "XGENT-WDS.exe"
        assert expected_manifest_digest == "b" * 64
        asset_path.write_bytes(b"new-agent" * 131072)
        return release_tag

    monkeypatch.setattr(module, "download_verified_release_asset", download)
    return guardian, progress, results


def test_guardian_replaces_frozen_agent_only_after_signed_download_and_keeps_backup(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    guardian, progress, results = _frozen_update_guardian(module, monkeypatch, tmp_path, [True])
    guardian._update_agent_package({
        "id": "a" * 32, "release_tag": "v4.2.0", "manifest_digest": "b" * 64,
        "component": "windows_agent",
    })
    install = tmp_path / "install"

    assert (install / "XGENT-WDS.exe").read_bytes() == b"new-agent" * 131072
    assert (install / f".XGENT-WDS.{ 'a' * 32 }.backup").read_bytes() == b"old-agent"
    assert progress == ["downloading", "verifying", "installing", "reconnecting", "health_check"]
    assert results[-1]["update_state"] == "succeeded"
    assert results[-1]["component"] == "windows_agent"
    assert guardian._active_update_id is None


def test_guardian_restores_previous_frozen_agent_when_new_health_fails(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    guardian, progress, results = _frozen_update_guardian(module, monkeypatch, tmp_path, [False, True])
    guardian._update_agent_package({
        "id": "a" * 32, "release_tag": "v4.2.0", "manifest_digest": "b" * 64,
        "component": "windows_agent",
    })

    assert (tmp_path / "install" / "XGENT-WDS.exe").read_bytes() == b"old-agent"
    assert "rolling_back" in progress
    assert results[-1]["update_state"] == "rolled_back"
    assert guardian._active_update_id is None


def test_frozen_update_terminal_receipt_survives_restart_and_replays_duplicate(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path / "config")
    operation = "a" * 32
    receipt = module._persist_update_receipt({
        "ok": True, "text": "update completed", "component": "windows_agent",
        "operation_id": operation, "release_tag": "v4.2.0",
        "manifest_digest": "b" * 64, "update_state": "succeeded",
        "updated_at": time.time(),
    })

    guardian = object.__new__(module.Guardian)
    guardian._update_lock = threading.Lock()
    guardian._active_update_id = None
    guardian.lock = threading.RLock()
    guardian.state = {"desired_running": True}
    guardian.status_payload = lambda: {"type": "guardian", "ok": True}
    published = []
    guardian._publish = published.append
    monkeypatch.setattr(module.threading, "Thread", lambda **_kwargs: pytest.fail("duplicate update must not start"))

    guardian.handle({
        "command": "update_agent", "id": operation,
        "release_tag": "v4.2.0", "manifest_digest": "b" * 64,
        "component": "windows_agent",
    })

    assert published[-1]["update_state"] == "succeeded"
    assert published[-1]["operation_id"] == operation
    assert published[-1]["text"] == receipt["text"]


def test_guardian_replays_latest_terminal_update_receipt_on_mqtt_reconnect(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path / "config")
    module._persist_update_receipt({
        "ok": False, "text": "rolled back", "component": "windows_agent",
        "operation_id": "c" * 32, "release_tag": "v4.2.0",
        "manifest_digest": "d" * 64, "update_state": "rolled_back",
        "updated_at": time.time(),
    })
    guardian = object.__new__(module.Guardian)
    guardian._mqtt_connected = False
    guardian.client = Mock()
    guardian.status_payload = Mock(return_value={"type": "guardian", "ok": True})
    published = []
    guardian._publish = published.append
    monkeypatch.setattr(module, "write_runtime_health", lambda *_args: None)

    guardian._on_connect(guardian.client, None, None, 0)

    assert len(published) == 2
    assert published[0]["type"] == "guardian"
    assert published[1]["operation_id"] == "c" * 32
    assert published[1]["update_state"] == "rolled_back"


def test_guardian_recovers_interrupted_exe_swap_from_durable_backup(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    install = tmp_path / "install"
    config_dir = tmp_path / "config"
    install.mkdir()
    config_dir.mkdir()
    live = install / "XGENT-WDS.exe"
    live.write_bytes(b"partially-installed")
    operation = "e" * 32
    backup = install / f".XGENT-WDS.{operation}.backup"
    backup.write_bytes(b"previous-working-agent")
    monkeypatch.setattr(module, "SCRIPT_DIR", install)
    monkeypatch.setattr(module, "CONFIG_DIR", config_dir)
    module._atomic_json(module._update_journal_path(), {
        "schema": 1, "component": "windows_agent", "operation_id": operation,
        "release_tag": "v4.2.0", "manifest_digest": "f" * 64,
        "phase": "installing", "updated_at": time.time(),
    })
    guardian = object.__new__(module.Guardian)
    guardian.state = {"desired_running": True}
    guardian.lock = threading.RLock()
    guardian._refresh_worker_intent = lambda: None
    guardian._stop_agent_for_update = lambda: None
    guardian.start_agent = lambda: 456
    guardian._wait_for_agent_health = lambda *_args: True
    guardian._publish = Mock()

    guardian._recover_interrupted_agent_update()

    assert live.read_bytes() == b"previous-working-agent"
    assert not module._update_journal_path().exists()
    assert guardian._publish.call_args.args[0]["update_state"] == "rolled_back"
    receipt = module._read_update_receipt(module._update_receipt_path(operation))
    assert receipt["update_state"] == "rolled_back"


def test_guardian_completes_receipt_for_committed_update_after_restart(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path / "config")
    operation = "9" * 32
    module._atomic_json(module._update_journal_path(), {
        "schema": 1, "component": "windows_agent", "operation_id": operation,
        "release_tag": "v4.2.0", "manifest_digest": "a" * 64,
        "phase": "committed", "updated_at": time.time(),
    })
    guardian = object.__new__(module.Guardian)
    guardian._publish = Mock()

    guardian._recover_interrupted_agent_update()

    assert not module._update_journal_path().exists()
    assert guardian._publish.call_args.args[0]["update_state"] == "succeeded"
    assert module._read_update_receipt(module._update_receipt_path(operation))["update_state"] == "succeeded"


def test_guardian_mqtt_callbacks_write_connected_and_disconnected_health(
    guardian_module, monkeypatch
):
    module = guardian_module
    written = []
    monkeypatch.setattr(
        module, "write_runtime_health",
        lambda component, connected: written.append((component, connected)),
    )
    guardian = object.__new__(module.Guardian)
    guardian.client = Mock()
    guardian._publish = Mock()
    guardian.status_payload = Mock(return_value={"type": "guardian"})

    guardian._on_connect(guardian.client, None, None, 0)
    guardian._on_disconnect(guardian.client, None, None, 7)

    assert written == [("guardian", True), ("guardian", False)]


def test_guardian_status_reports_agent_and_guardian_tasks_separately(
    guardian_module, monkeypatch
):
    module = guardian_module
    guardian = object.__new__(module.Guardian)
    guardian.state = {"auto_restart": True, "desired_running": True}
    queried = []
    monkeypatch.setattr(module, "_task_exists", lambda name: queried.append(name) or name == module.AGENT_TASK)
    monkeypatch.setattr(guardian, "agent_process", lambda: None)

    result = guardian.status_payload()

    assert result["launchd_loaded"] is True
    assert result["guardian_task_registered"] is False
    assert "Задача агента: зарегистрирована" in result["text"]
    assert "Задача Guardian: не зарегистрирована" in result["text"]
    assert queried == [module.AGENT_TASK, module.GUARDIAN_TASK]


def test_guardian_detects_only_its_agents_matching_stale_heartbeat(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    (tmp_path / "agent-health.json").write_text(json.dumps({
        "component": "agent", "pid": 4321,
        "updated_at": time.time() - module.AGENT_HEALTH_STALE_SECONDS - 1,
    }), encoding="utf-8")
    guardian = object.__new__(module.Guardian)

    assert guardian._agent_health_is_stale(4321) is True
    assert guardian._agent_health_is_stale(9876) is False


def test_guardian_status_keeps_task_query_errors_unknown(guardian_module, monkeypatch):
    module = guardian_module
    guardian = object.__new__(module.Guardian)
    guardian.state = {"auto_restart": True, "desired_running": True}
    monkeypatch.setattr(module, "_task_exists", lambda _name: None)
    monkeypatch.setattr(guardian, "agent_process", lambda: None)

    result = guardian.status_payload()

    assert result["launchd_loaded"] is None
    assert result["guardian_task_registered"] is None
    assert "Задача агента: статус неизвестен" in result["text"]
    assert "Задача Guardian: статус неизвестен" in result["text"]


def test_guardian_periodically_publishes_fresh_update_capability(guardian_module, monkeypatch):
    module = guardian_module
    guardian = object.__new__(module.Guardian)
    waits = iter((False, True))
    guardian.stop_event = Mock()
    guardian.stop_event.wait.side_effect = lambda _seconds: next(waits)
    guardian.lock = threading.RLock()
    guardian.state = {"auto_restart": False, "desired_running": True}
    guardian._mqtt_connected = True
    guardian._last_status_publish = 0.0
    guardian._refresh_worker_intent = Mock()
    guardian.status_payload = Mock(return_value={"agent_update_protocol": 1})
    published = []
    guardian._publish = published.append
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)

    guardian.monitor()

    assert published == [{"agent_update_protocol": 1}]
    assert guardian._last_status_publish == 100.0


def test_task_query_access_error_is_not_reported_as_missing(guardian_module, monkeypatch):
    module = guardian_module
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 1, "stdout": "Access is denied.", "stderr": ""}
        )(),
    )

    assert module._task_exists(module.AGENT_TASK) is None


def test_task_query_recognizes_missing_task(guardian_module, monkeypatch):
    module = guardian_module
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 1, "stdout": "ERROR: The system cannot find the file specified.", "stderr": ""}
        )(),
    )

    assert module._task_exists(module.AGENT_TASK) is False


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
    client._sysinfo_schedule_wake = threading.Event()
    client.on_stop_requested = None

    client.request_stop()

    assert saved == [False]
    assert not client._running.is_set()
    assert client._sysinfo_schedule_wake.is_set()


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


def test_removed_guardian_intent_blocks_windows_agent_recovery(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    _use_temp_state(monkeypatch, module, tmp_path)
    guardian = module.Guardian()
    module.STATE_FILE.unlink()
    monkeypatch.setattr(guardian, "agent_process", lambda: None)
    popen = Mock(side_effect=AssertionError("worker must not be started"))
    monkeypatch.setattr(module.subprocess, "Popen", popen)

    with pytest.raises(RuntimeError, match="Guardian state was removed"):
        guardian.start_agent()

    popen.assert_not_called()


def test_monitor_backs_off_when_recovered_agent_exits_immediately(
    guardian_module, monkeypatch
):
    module = guardian_module
    guardian = object.__new__(module.Guardian)
    waits = iter((False, False, True))
    guardian.stop_event = Mock()
    guardian.stop_event.wait.side_effect = lambda _seconds: next(waits)
    guardian.lock = threading.RLock()
    guardian.state = {"auto_restart": True, "desired_running": True}
    guardian._restart_backoff_seconds = 5.0
    guardian._next_restart_at = 0.0
    guardian._last_recovery_error = None
    guardian._observed_agent_pid = None
    guardian._observed_agent_since = 0.0
    monkeypatch.setattr(guardian, "_refresh_worker_intent", Mock())
    monkeypatch.setattr(guardian, "agent_process", Mock(return_value=None))
    monkeypatch.setattr(guardian, "start_agent", Mock(return_value=4321))
    published = []
    monkeypatch.setattr(guardian, "_publish", lambda payload: published.append(payload))
    ticks = iter((100.0, 100.0, 105.0, 105.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))

    guardian.monitor()

    guardian.start_agent.assert_called_once_with()
    assert guardian._next_restart_at == 110.0
    assert guardian._restart_backoff_seconds == 10.0
    assert [row["ok"] for row in published] == [True, False]
