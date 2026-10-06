"""Agent update dispatch contract tests; no GitHub or device files are changed."""

import json
from pathlib import Path
import threading
import zipfile
from unittest.mock import MagicMock

import pytest

import xgent_mcs as mcs
import update_package as package


def _make_client():
    client = object.__new__(mcs.XgentClient)
    client._running = threading.Event()
    client._running.set()
    client._update_health = threading.Event()
    client._update_lock = threading.Lock()
    client._lifecycle_lock = threading.RLock()
    client._update_cancelled = threading.Event()
    client._command_context = threading.local()
    client._pending_command_subacks = set()
    client._command_subscriptions_failed = False
    client.on_stop_requested = None
    return client


def _ack_command_subscriptions(client):
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(mcs.mqtt.MQTT_ERR_SUCCESS, 41), (mcs.mqtt.MQTT_ERR_SUCCESS, 42)]
    client._on_connect(mqtt_client, None, None, 0)
    client._on_subscribe(mqtt_client, None, 41, [0])
    client._on_subscribe(mqtt_client, None, 42, [0])
    return mqtt_client


def test_geo_location_has_one_bounded_response(monkeypatch):
    import io
    import urllib.request

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    clock = [100.0]
    timeouts = []
    published = []

    def fake_monotonic():
        return clock[0]

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        clock[0] += timeout
        raise TimeoutError("fixture timeout")

    client = _make_client()
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(mcs.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client._do_geo_location({"id": "geo-123"})

    assert timeouts == [4.0, 3.0]
    assert len(published) == 1
    assert published[0][0] == "geo_location"
    assert published[0][1]["type"] == "geo_location"
    assert published[0][1]["ok"] is False


def test_agent_update_passes_release_tag_to_verifier_and_fails_closed(monkeypatch, tmp_path):
    client = _make_client()
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("XIDER_UPDATE_BRANCH", "attacker-controlled-branch")
    observed = {}

    def reject_unpublished_release(manifest_path, archive_path, *, release_tag=None):
        observed.update(
            manifest=Path(manifest_path).name,
            archive=Path(archive_path).name,
            release_tag=release_tag,
        )
        raise ValueError("В релизе нет единственной пары source ZIP и release manifest.")

    monkeypatch.setattr(mcs, "download_verified_source_archive", reject_unpublished_release)

    client._do_agent_update({"update": True, "release_tag": "v4.2.0"})

    assert observed == {
        "manifest": "release-manifest.json",
        "archive": "XIDER-source.zip",
        "release_tag": "v4.2.0",
    }
    assert sent[0][0] == "output"
    assert sent[0][1]["type"] == "agent_update"
    assert sent[0][1]["ok"] is False
    assert "в релизе нет" in sent[0][1]["text"].lower()
    assert not (tmp_path / "agent-backups").exists()


def test_agent_status_request_does_not_fetch_release(monkeypatch):
    client = _make_client()
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(
        mcs,
        "download_verified_source_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not be used")),
    )

    client._do_agent_update({"update": False})

    assert sent[0][1]["ok"] is True
    assert "Статус: 🟢 онлайн" in sent[0][1]["text"]


def test_mac_status_advertises_source_update_mode(monkeypatch):
    client = _make_client()
    client._client = MagicMock()
    monkeypatch.setattr(mcs, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(mcs.sys, "frozen", False, raising=False)
    monkeypatch.setattr(mcs, "trusted_release_keys_ready", lambda: False)

    client._publish_status()

    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = mcs.verify_message(envelope)
    assert payload["update_mode"] == "source"
    assert payload["release_update_ready"] is False


def test_mac_status_advertises_release_key_readiness(monkeypatch):
    client = _make_client()
    client._client = MagicMock()
    monkeypatch.setattr(mcs, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(mcs.sys, "frozen", False, raising=False)
    monkeypatch.setattr(mcs, "trusted_release_keys_ready", lambda: True)

    client._publish_status()

    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = mcs.verify_message(envelope)
    assert payload["release_update_ready"] is True


def test_frozen_agent_update_fails_closed_without_mutating_bundle(monkeypatch, tmp_path):
    client = _make_client()
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mcs.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        mcs,
        "download_verified_source_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must fail before download")),
    )

    client._do_agent_update({"update": True})

    assert sent[0][1]["ok"] is False
    assert sent[0][1]["state"] == "unsupported_package"
    assert "ничего не менял" in sent[0][1]["text"].lower()
    assert list(tmp_path.iterdir()) == []


def test_mqtt_health_requires_both_successful_command_subacks(monkeypatch, tmp_path):
    client = _make_client()
    client._update_health = threading.Event()
    client._publish_status = lambda: None
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)

    backup = tmp_path / package.UPDATE_BACKUP_DIR / "previous"
    backup.mkdir(parents=True)
    (backup / "xgent_mcs.py").write_text("previous agent", encoding="utf-8")
    state_path = tmp_path / package.UPDATE_STATE_NAME
    state_path.write_text(json.dumps({
        "schema": 1,
        "status": "awaiting_health",
        "install_dir": str(Path(mcs.__file__).resolve().parent),
        "backup_dir": str(backup),
        "files": ["xgent_mcs.py"],
        "existing": ["xgent_mcs.py"],
        "start_attempts": 1,
    }), encoding="utf-8")
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(0, 41), (0, 42)]
    guardian_restarts = []

    def restart_after_commit():
        assert not state_path.exists()
        assert client._update_health.is_set()
        guardian_restarts.append(True)

    client._restart_existing_guardian_after_update = restart_after_commit

    client._on_connect(mqtt_client, None, None, 0)

    assert not client._update_health.is_set()
    assert state_path.exists()
    assert guardian_restarts == []
    client._on_subscribe(mqtt_client, None, 999, [0])
    client._on_subscribe(mqtt_client, None, 41, [0])
    client._on_subscribe(mqtt_client, None, 41, [0])
    assert state_path.exists()
    assert not client._update_health.is_set()
    client._on_subscribe(mqtt_client, None, 42, [0])

    assert client._update_health.is_set()
    assert not state_path.exists()
    assert mqtt_client.subscribe.call_count == 2
    assert guardian_restarts == [True]


@pytest.fixture
def source_update(monkeypatch, tmp_path):
    """Real ZIP extraction, file swaps and journal; deferred process restart."""
    install = tmp_path / "install"
    install.mkdir()
    state_root = tmp_path / "state"
    monkeypatch.setattr(mcs, "__file__", str(install / "xgent_mcs.py"))
    monkeypatch.setattr(mcs, "CONFIG_DIR", state_root)
    monkeypatch.setattr(mcs.sys, "frozen", False, raising=False)
    files = (
        "release_signature.py", "update_package.py", "guardian_recovery.py", "start_agent.sh",
        "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
        "fake_update_screen.py",
        "xider_guardian.py", "requirements.txt", "setup_mac.py",
        "stop_agent.sh", "start_guardian.sh",
    )
    candidates = {name: b"# new signed source\n" for name in files}
    candidates["requirements.txt"] = b"paho-mqtt>=2.0,<3\n"
    for name in files:
        (install / name).write_bytes(
            candidates[name] if name == "requirements.txt" else b"# previous source\n"
        )
    sent, cache_calls, restarts = [], [], []
    client = _make_client()
    client._publish_response = lambda topic, payload: sent.append((topic, payload))

    def download(manifest, archive, *, release_tag=None):
        Path(manifest).write_text("{}", encoding="utf-8")
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, content in candidates.items():
                bundle.writestr(f"XIDER/XGENT-MCS/{name}", content)
        return release_tag or "v4.2.0"

    def cache(manifest, archive, recovery_root, *, activate=True):
        assert Path(manifest).is_file() and Path(archive).is_file()
        cache_calls.append((Path(recovery_root), activate))
        return "v4.2.0-" + "a" * 64

    class DeferredThread:
        def __init__(self, *, target, args=(), **kwargs):
            self.target, self.args = target, args

        def start(self):
            restarts.append(lambda: self.target(*self.args))

    monkeypatch.setattr(mcs, "download_verified_source_archive", download)
    monkeypatch.setattr(mcs, "stage_recovery_package", cache)
    monkeypatch.setattr(mcs.threading, "Thread", DeferredThread)
    monkeypatch.setattr(mcs.time, "sleep", lambda *_args: None)
    return client, install, state_root, candidates, sent, cache_calls, restarts


def test_source_update_serializes_until_exec_and_does_not_spawn_guardian(source_update, monkeypatch):
    client, install, state_root, _, sent, cache_calls, restarts = source_update
    execs = []
    monkeypatch.setattr(mcs.os, "execv", lambda executable, args: execs.append((executable, args)))
    monkeypatch.setattr(mcs.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("restart must use exec"))
    monkeypatch.setattr(mcs.sys, "argv", ["xgent_mcs.py", "--example"])

    client._do_agent_update({"release_tag": "v4.2.0"})
    client._do_agent_update({"release_tag": "v4.3.0"})

    assert sent[0][1]["state"] == "restarting"
    assert sent[1][1]["state"] == "update_busy"
    assert cache_calls == [(state_root / "recovery", False)]
    state = json.loads((state_root / package.UPDATE_STATE_NAME).read_text(encoding="utf-8"))
    assert state["recovery_slot"] == "v4.2.0-" + "a" * 64
    assert (install / "guardian_recovery.py").read_bytes() == b"# new signed source\n"
    assert len(restarts) == 1
    restarts[0]()
    assert execs == [(mcs.sys.executable, [mcs.sys.executable, str(install / "xgent_mcs.py"), "--example"])]
    assert not client._update_lock.locked()


def test_dependency_change_keeps_running_source_and_environment(source_update):
    client, install, state_root, candidates, sent, cache_calls, restarts = source_update
    candidates["requirements.txt"] += b"new-runtime-package>=3\n"

    client._do_agent_update({"release_tag": "v4.2.0"})

    assert sent[-1][1]["state"] == "dependency_install_required"
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert (install / "requirements.txt").read_bytes() == b"paho-mqtt>=2.0,<3\n"
    assert not (state_root / package.UPDATE_STATE_NAME).exists()
    assert not (state_root / package.UPDATE_BACKUP_DIR).exists()
    assert not cache_calls and not restarts
    assert client._running.is_set() and not client._update_lock.locked()


def test_exec_failure_rolls_back_and_keeps_current_worker_running(source_update, monkeypatch):
    client, install, state_root, _, sent, _, restarts = source_update
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: (_ for _ in ()).throw(OSError("exec failed")))

    client._do_agent_update({"release_tag": "v4.2.0"})
    restarts[0]()

    assert sent[-1][1]["state"] == "restart_failed"
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert not (state_root / package.UPDATE_STATE_NAME).exists()
    assert client._running.is_set() and not client._update_lock.locked()


def test_explicit_stop_rolls_back_and_cancels_scheduled_restart(source_update, monkeypatch):
    client, install, state_root, _, _, _, restarts = source_update
    saved = []
    monkeypatch.setattr(mcs, "set_guardian_desired_running", saved.append)
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: pytest.fail("explicit stop must cancel restart"))

    client._do_agent_update({"release_tag": "v4.2.0"})
    client.request_stop()
    restarts[0]()

    assert saved == [False]
    assert not client._running.is_set()
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert not (state_root / package.UPDATE_STATE_NAME).exists()
    assert not client._update_lock.locked()


def test_stop_during_download_prevents_file_swap(source_update, monkeypatch):
    client, install, state_root, _, sent, _, restarts = source_update
    original_download = mcs.download_verified_source_archive
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)

    def stop_during_download(*args, **kwargs):
        result = original_download(*args, **kwargs)
        client.request_stop()
        return result

    monkeypatch.setattr(mcs, "download_verified_source_archive", stop_during_download)
    client._do_agent_update({"release_tag": "v4.2.0"})

    assert sent[-1][1]["ok"] is False
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert not (state_root / package.UPDATE_STATE_NAME).exists()
    assert not restarts


def test_old_worker_reconnect_cannot_commit_pending_update(source_update):
    client, _, state_root, _, _, _, _ = source_update
    client._publish_status = lambda: None
    client._do_agent_update({"release_tag": "v4.2.0"})

    _ack_command_subscriptions(client)

    assert not client._update_health.is_set()
    assert (state_root / package.UPDATE_STATE_NAME).exists()


def test_new_worker_health_activates_cache_before_committing(source_update, monkeypatch):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    assert package.prepare_agent_update_start(state_root / package.UPDATE_STATE_NAME, install) == "pending"
    new = _make_client()
    new._publish_status = lambda: None
    new._restart_existing_guardian_after_update = lambda: None
    activations = []
    monkeypatch.setattr(mcs, "validate_recovery_install", lambda *_args: "v4.2.0")
    monkeypatch.setattr(mcs, "activate_recovery_package", lambda root, slot: activations.append((root, slot)))

    _ack_command_subscriptions(new)

    assert activations == [(state_root / "recovery", "v4.2.0-" + "a" * 64)]
    assert new._update_health.is_set()
    assert not (state_root / package.UPDATE_STATE_NAME).exists()


def test_health_timeout_does_not_restart_after_explicit_stop(monkeypatch, tmp_path):
    client = _make_client()
    client._update_cancelled.set()
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mcs, "rollback_unhealthy_agent", lambda *_args: pytest.fail("stop must cancel watchdog"))
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: pytest.fail("stop must cancel watchdog"))

    client._rollback_if_update_unhealthy(timeout=0)


def test_health_timeout_rolls_back_and_execs_previous_source(source_update, monkeypatch):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    package.prepare_agent_update_start(state_root / package.UPDATE_STATE_NAME, install)
    new = _make_client()
    execs = []
    monkeypatch.setattr(mcs.os, "execv", lambda *args: execs.append(args))

    new._rollback_if_update_unhealthy(timeout=0)

    assert len(execs) == 1
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert not (state_root / package.UPDATE_STATE_NAME).exists()


def test_uninstall_during_restart_delay_cannot_recreate_agent(source_update, monkeypatch, tmp_path):
    client, _, state_root, _, sent, _, restarts = source_update
    monkeypatch.setattr(mcs.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: MagicMock(returncode=0))
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: pytest.fail("uninstall must cancel update restart"))

    client._do_agent_update({"release_tag": "v4.2.0"})
    client._do_uninstall_agent({})
    restarts[0]()

    assert sent[-1][1]["type"] == "uninstall_agent"
    assert sent[-1][1]["ok"] is True
    assert not state_root.exists()
    assert client._update_cancelled.is_set()
    assert not client._update_lock.locked()


def test_restart_thread_failure_restores_source_immediately(source_update, monkeypatch):
    client, install, state_root, _, sent, _, _ = source_update

    class FailingThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("cannot start restart thread")

    monkeypatch.setattr(mcs.threading, "Thread", FailingThread)
    client._do_agent_update({"release_tag": "v4.2.0"})

    assert sent[-1][1]["state"] == "update_failed"
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert not (state_root / package.UPDATE_STATE_NAME).exists()
    assert not client._update_lock.locked()


def test_stop_intent_precedes_launchd_unload_and_process_exit(monkeypatch, tmp_path):
    client = _make_client()
    plist = tmp_path / "worker.plist"
    plist.write_text("fixture", encoding="utf-8")
    intents, commands = [], []
    monkeypatch.setattr(mcs.os.path, "expanduser", lambda _path: str(plist))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(mcs, "set_guardian_desired_running", intents.append)

    def unload(args, **kwargs):
        commands.append(args)
        assert intents == [False]
        assert not plist.exists()
        assert client._running.is_set()
        assert client._update_cancelled.is_set()
        return MagicMock(returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", unload)
    client._do_stop({})

    assert commands == [["launchctl", "bootout", "gui/501/com.xgent.agent"]]
    assert not client._running.is_set()


def test_failed_launchd_stop_keeps_agent_reachable(monkeypatch, tmp_path):
    client = _make_client()
    sent = []
    client._publish_response = lambda _topic, payload: sent.append(payload)
    monkeypatch.setattr(mcs.os.path, "expanduser", lambda _path: str(tmp_path / "absent.plist"))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)
    results = iter((MagicMock(returncode=1), MagicMock(returncode=0)))
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: next(results))

    client._do_stop({})

    assert client._running.is_set()
    assert sent[-1]["type"] == "stop" and sent[-1]["ok"] is False


def test_uninstall_unloads_existing_worker_job_after_ack(source_update, monkeypatch, tmp_path):
    client, _, _, _, sent, _, restarts = source_update
    commands, exits = [], []
    monkeypatch.setattr(mcs.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)

    def unload(args, **kwargs):
        commands.append(args)
        if args[-1] == "gui/501/com.xgent.agent":
            assert sent[-1][1]["type"] == "uninstall_agent"
        return MagicMock(returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", unload)
    monkeypatch.setattr(mcs.os, "_exit", exits.append)
    client._do_uninstall_agent({})
    response = sent[-1][1]
    assert response["state"] == "uninstall_prepared"
    assert ".env" in response["text"] and "agent.log" in response["text"]
    assert "полностью удалён" not in response["text"]
    restarts[0]()

    assert commands[-1] == ["launchctl", "bootout", "gui/501/com.xgent.agent"]
    assert exits == [0]


@pytest.mark.parametrize("raises", [False, True])
def test_uninstall_does_not_report_success_when_config_remains(source_update, monkeypatch, tmp_path, raises):
    client, _, state_root, _, sent, _, restarts = source_update
    state_root.mkdir(parents=True)
    marker = state_root / "keep.txt"
    marker.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(mcs.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: MagicMock(returncode=0))

    def cannot_delete(_path, **kwargs):
        if raises:
            raise PermissionError("cannot remove config")

    monkeypatch.setattr(mcs.shutil, "rmtree", cannot_delete)
    client._do_uninstall_agent({})

    assert marker.exists()
    assert len(sent) == 1
    assert sent[0][1]["ok"] is False
    assert sent[0][1]["state"] == "uninstall_incomplete"
    assert not restarts
    assert client._running.is_set()


def test_cache_verification_failure_does_not_commit_new_agent(source_update, monkeypatch):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    state_path = state_root / package.UPDATE_STATE_NAME
    package.prepare_agent_update_start(state_path, install)
    new = _make_client()
    new._publish_status = lambda: None
    monkeypatch.setattr(
        mcs, "validate_recovery_install",
        lambda *_args: (_ for _ in ()).throw(ValueError("installed source changed")),
    )
    monkeypatch.setattr(mcs, "activate_recovery_package", lambda *_args: pytest.fail("do not select unverified cache"))
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: None)

    _ack_command_subscriptions(new)

    assert state_path.exists()
    assert not new._update_health.is_set()
    new._rollback_if_update_unhealthy(timeout=0)
    assert not state_path.exists()
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"


@pytest.mark.parametrize("loaded", [False, True])
def test_committed_update_only_restarts_an_existing_guardian_job(monkeypatch, loaded):
    client = _make_client()
    client._update_health.set()
    commands = []
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)

    def launchctl(args, **kwargs):
        commands.append(args)
        assert kwargs["timeout"] == 10
        if args[1] == "print":
            return MagicMock(returncode=0 if loaded else 1)
        return MagicMock(returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", launchctl)
    client._restart_existing_guardian_after_update()

    expected = [["launchctl", "print", "gui/501/com.xider.guardian"]]
    if loaded:
        expected.append(["launchctl", "kickstart", "-k", "gui/501/com.xider.guardian"])
    assert commands == expected


def test_guardian_restart_failure_keeps_worker_health_committed(monkeypatch):
    client = _make_client()
    client._update_health.set()
    sent = []
    client._publish_response = lambda _topic, payload: sent.append(payload)
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    results = iter((MagicMock(returncode=0), MagicMock(returncode=1, stderr="kickstart failed")))
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: next(results))

    client._restart_existing_guardian_after_update()

    assert client._update_health.is_set()
    assert sent == [{
        "type": "agent_update", "device_id": mcs.DEVICE_ID, "ok": True,
        "state": "guardian_restart_failed", "health_check": "passed", "guardian_restart": "failed",
        "text": "⚠️ Обновление агента подтверждено, MQTT работает. Перезапуск Guardian не выполнен: kickstart failed. Guardian может продолжать работу со старым кодом.",
    }]


def test_explicit_stop_prevents_guardian_restart_after_commit(monkeypatch):
    client = _make_client()
    client._update_health.set()
    client._update_cancelled.set()
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("stop must not restart Guardian"))

    client._restart_existing_guardian_after_update()


def test_delayed_restart_failure_preserves_original_command_id(source_update, monkeypatch):
    client, _, _, _, sent, _, restarts = source_update
    client._command_context.cmd_id = "update-command-123"
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: (_ for _ in ()).throw(OSError("exec failed")))
    client._do_agent_update({"release_tag": "v4.2.0"})
    del client._command_context.cmd_id

    restarts[0]()

    assert sent[-1][1]["state"] == "restart_failed"
    assert sent[-1][1]["id"] == "update-command-123"


def test_delayed_uninstall_failure_preserves_original_command_id(source_update, monkeypatch, tmp_path):
    client, _, _, _, sent, _, restarts = source_update
    client._command_context.cmd_id = "uninstall-command-123"
    monkeypatch.setattr(mcs.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcs.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)
    results = iter((MagicMock(returncode=0), MagicMock(returncode=1), MagicMock(returncode=0)))
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(mcs.os, "_exit", lambda *_args: pytest.fail("failed unload must keep worker reachable"))
    client._do_uninstall_agent({})
    del client._command_context.cmd_id

    restarts[0]()

    assert sent[-1][1]["state"] == "uninstall_incomplete"
    assert sent[-1][1]["ok"] is False
    assert sent[-1][1]["id"] == "uninstall-command-123"


def test_failed_guardian_stop_intent_keeps_network_agent_reachable(monkeypatch):
    client = _make_client()
    sent = []
    client._publish_response = lambda _topic, payload: sent.append(payload)
    monkeypatch.setattr(
        mcs, "set_guardian_desired_running",
        lambda _running: (_ for _ in ()).throw(PermissionError("guardian state is read-only")),
    )
    monkeypatch.setattr(mcs.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("do not bootout without persisted stop intent"))
    client._cancel_pending_update = lambda **_kwargs: pytest.fail("do not cancel before stop intent is saved")

    client._do_stop({})

    assert client._running.is_set()
    assert not client._update_cancelled.is_set()
    assert sent[-1]["ok"] is False and sent[-1]["state"] == "stop_failed"


@pytest.mark.parametrize("rejection", [[128], [], [0, 0], [MagicMock(value=128)]])
def test_rejected_suback_keeps_journal_until_old_source_is_restored(source_update, monkeypatch, rejection):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    state_path = state_root / package.UPDATE_STATE_NAME
    package.prepare_agent_update_start(state_path, install)
    new = _make_client()
    new._publish_status = lambda: None
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(0, 41), (0, 42)]
    monkeypatch.setattr(mcs, "activate_recovery_package", lambda *_args: pytest.fail("rejected subscription cannot select new cache"))
    execs = []
    monkeypatch.setattr(mcs.os, "execv", lambda *args: execs.append(args))

    new._on_connect(mqtt_client, None, None, 0)
    new._on_subscribe(mqtt_client, None, 41, [0])
    new._on_subscribe(mqtt_client, None, 42, rejection)

    assert state_path.exists() and not new._update_health.is_set()
    new._rollback_if_update_unhealthy(timeout=0)
    assert not state_path.exists()
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"
    assert len(execs) == 1


@pytest.mark.parametrize("failed_topic", [0, 1])
def test_subscribe_send_failure_cannot_confirm_health(source_update, monkeypatch, failed_topic):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    state_path = state_root / package.UPDATE_STATE_NAME
    package.prepare_agent_update_start(state_path, install)
    new = _make_client()
    new._publish_status = lambda: None
    mqtt_client = MagicMock()
    subscriptions = [(0, 41), (0, 42)]
    subscriptions[failed_topic] = (mcs.mqtt.MQTT_ERR_NO_CONN, None)
    mqtt_client.subscribe.side_effect = subscriptions
    monkeypatch.setattr(mcs.os, "execv", lambda *_args: None)

    new._on_connect(mqtt_client, None, None, 0)
    new._on_subscribe(mqtt_client, None, 42 if failed_topic == 0 else 41, [0])

    assert state_path.exists() and not new._update_health.is_set()
    new._rollback_if_update_unhealthy(timeout=0)
    assert not state_path.exists()
    assert (install / "xgent_mcs.py").read_bytes() == b"# previous source\n"


def test_disconnect_discards_old_subacks_and_requires_new_confirmations(source_update, monkeypatch):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    state_path = state_root / package.UPDATE_STATE_NAME
    package.prepare_agent_update_start(state_path, install)
    new = _make_client()
    new._publish_status = lambda: None
    new._restart_existing_guardian_after_update = lambda: None
    monkeypatch.setattr(mcs, "validate_recovery_install", lambda *_args: "v4.2.0")
    monkeypatch.setattr(mcs, "activate_recovery_package", lambda *_args: "v4.2.0")
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(0, 41), (0, 42), (0, 43), (0, 44)]

    new._on_connect(mqtt_client, None, None, 0)
    new._on_disconnect(mqtt_client, None, None, 1)
    new._on_subscribe(mqtt_client, None, 41, [0])
    new._on_subscribe(mqtt_client, None, 42, [0])
    assert state_path.exists()
    new._on_connect(mqtt_client, None, None, 0)
    new._on_subscribe(mqtt_client, None, 41, [0])
    new._on_subscribe(mqtt_client, None, 43, [MagicMock(value=0)])
    assert state_path.exists()
    new._on_subscribe(mqtt_client, None, 44, [1])

    assert new._update_health.is_set()
    assert not state_path.exists()


def test_explicit_stop_during_suback_wait_prevents_commit(source_update, monkeypatch):
    old, install, state_root, _, _, _, _ = source_update
    old._do_agent_update({"release_tag": "v4.2.0"})
    state_path = state_root / package.UPDATE_STATE_NAME
    package.prepare_agent_update_start(state_path, install)
    new = _make_client()
    new._publish_status = lambda: None
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(0, 41), (0, 42)]
    monkeypatch.setattr(mcs, "set_guardian_desired_running", lambda _running: None)
    monkeypatch.setattr(mcs, "activate_recovery_package", lambda *_args: pytest.fail("stop must prevent cache selection"))

    new._on_connect(mqtt_client, None, None, 0)
    new.request_stop()
    new._on_subscribe(mqtt_client, None, 41, [0])
    new._on_subscribe(mqtt_client, None, 42, [0])

    assert state_path.exists()
    assert not new._update_health.is_set()
