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
    for name in ("xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py", "release_signature.py", "update_package.py"):
        (tmp_path / name).write_text("# test payload\n", encoding="utf-8")
    guardian = object.__new__(module.Guardian)

    assert guardian._restore_missing_agent_files() == []
    assert not (tmp_path / ".env").exists()


def test_guardian_does_not_recreate_env_when_required_settings_are_unavailable(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    for name in ("xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py", "release_signature.py", "update_package.py"):
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
    monkeypatch.setattr(module, "_save_state", lambda _state, *_fields: None)
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


def test_concurrent_guardian_state_updates_merge_without_losing_fields(
    guardian_module, monkeypatch, tmp_path
):
    from concurrent.futures import ThreadPoolExecutor
    import config

    module = guardian_module
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)

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
    state_file = tmp_path / "guardian.json"
    monkeypatch.setattr(module, "STATE_FILE", state_file)
    state_file.write_text(
        json.dumps({"state_version": 2, "desired_running": False, "startup_enabled": True}),
        encoding="utf-8",
    )
    guardian = object.__new__(module.Guardian)
    guardian.state = {"desired_running": True, "startup_enabled": True}

    guardian._refresh_worker_intent()

    assert guardian.state["desired_running"] is False
    assert guardian.state["startup_enabled"] is True


def test_explicit_worker_stop_persists_guardian_intent(guardian_module, monkeypatch):
    import threading
    import xgent_mcs as mcs

    saved = []
    monkeypatch.setattr(mcs, "set_guardian_desired_running", saved.append)
    client = object.__new__(mcs.XgentClient)
    client._running = threading.Event()
    client._running.set()
    client.on_stop_requested = None

    client.request_stop()

    assert saved == [False]
    assert not client._running.is_set()


def test_uninstall_unloads_guardian_before_removing_worker_and_config(
    guardian_module, monkeypatch, tmp_path
):
    import xgent_mcs as mcs

    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path / "config")
    mcs.CONFIG_DIR.mkdir()
    monkeypatch.setattr(mcs.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    for name in ("com.xgent.agent.plist", "com.xider.guardian.plist"):
        (launch_agents / name).write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda enabled: None)
    calls = []
    monkeypatch.setattr(
        mcs.subprocess,
        "run",
        lambda args, **kwargs: calls.append(list(args)) or Mock(returncode=0, stderr=""),
    )

    class DeferredThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(mcs.threading, "Thread", DeferredThread)
    client = object.__new__(mcs.XgentClient)
    published = []
    client._publish_response = lambda _topic, payload: published.append(payload)

    client._do_uninstall_agent({})

    bootouts = [call for call in calls if call[:2] == ["launchctl", "bootout"]]
    assert len(bootouts) == 1
    assert bootouts[0][-1].endswith("com.xider.guardian.plist")
    assert not (launch_agents / "com.xgent.agent.plist").exists()
    assert not (launch_agents / "com.xider.guardian.plist").exists()
    assert not mcs.CONFIG_DIR.exists()
    assert published[0]["type"] == "uninstall_agent"


def test_uninstall_preserves_config_if_guardian_cannot_be_unloaded(
    guardian_module, monkeypatch, tmp_path
):
    import xgent_mcs as mcs

    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path / "config")
    mcs.CONFIG_DIR.mkdir()
    marker = mcs.CONFIG_DIR / "preserve-me"
    marker.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(mcs.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    guardian_plist = launch_agents / "com.xider.guardian.plist"
    guardian_plist.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda enabled: None)
    results = iter((Mock(returncode=1, stderr="permission denied"), Mock(returncode=0, stderr="")))
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: next(results))
    client = object.__new__(mcs.XgentClient)
    published = []
    client._publish_response = lambda _topic, payload: published.append(payload)

    client._do_uninstall_agent({})

    assert marker.exists()
    assert guardian_plist.exists()
    assert published[0]["ok"] is False
    assert "удаление отменено" in published[0]["text"]


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
