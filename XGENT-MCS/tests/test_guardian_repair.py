import json
import hashlib
import threading
import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def guardian_module(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("SHARED_KEY", "test-only-shared-key-guard-0123456789")
    monkeypatch.setenv("MQTT_BROKER", "localhost")
    monkeypatch.setenv("MQTT_PORT", "8883")
    monkeypatch.setenv("MQTT_TLS", "true")
    monkeypatch.setenv("ENCRYPT_PAYLOAD", "true")
    monkeypatch.setenv("MQTT_PREFIX", "xgent/test")
    monkeypatch.setenv("MQTT_USERNAME", "test-user")
    monkeypatch.setenv("MQTT_PASSWORD", "test-password")
    import xider_guardian
    import config

    state_dir = tmp_path_factory.mktemp("guardian-state")
    monkeypatch.setattr(config, "CONFIG_DIR", state_dir)
    monkeypatch.setattr(xider_guardian, "CONFIG_DIR", state_dir)
    monkeypatch.setattr(xider_guardian, "STATE_FILE", state_dir / "guardian.json")

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
    for name in module.RECOVERY_FILES:
        (tmp_path / name).write_text("# test payload\n", encoding="utf-8")
    guardian = object.__new__(module.Guardian)

    assert guardian._restore_missing_agent_files() == []
    assert not (tmp_path / ".env").exists()


def test_guardian_does_not_recreate_env_when_required_settings_are_unavailable(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    for name in module.RECOVERY_FILES:
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


@pytest.fixture
def signed_recovery(guardian_module, monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import guardian_recovery as recovery
    import release_signature

    key = Ed25519PrivateKey.generate()
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setattr(release_signature, "TRUSTED_RELEASE_KEYS", {
        release_signature.release_key_id(public_bytes): public_bytes,
    })
    archive = tmp_path / "source.zip"
    payloads = {name: f"# signed recovery source: {name}\n".encode() for name in recovery.RECOVERY_FILES}
    with zipfile.ZipFile(archive, "w") as output:
        for name, payload in payloads.items():
            output.writestr(f"XIDER-fixture/XGENT-MCS/{name}", payload)
    archive_bytes = archive.read_bytes()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(release_signature.sign_manifest({
        "schema": 1, "release": "v4.1.0", "assets": [{
            "name": "XIDER-source.zip", "component": "source", "platform": "all",
            "size": len(archive_bytes), "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }],
    }, private_pem)), encoding="utf-8")
    recovery_root = guardian_module.CONFIG_DIR / "recovery"
    return recovery, manifest, archive, recovery_root, payloads


def test_signed_package_recovers_missing_worker_without_overwriting_present_files(
    guardian_module, signed_recovery, monkeypatch, tmp_path
):
    recovery, manifest, archive, recovery_root, payloads = signed_recovery
    slot = recovery.stage_recovery_package(manifest, archive, recovery_root)
    install = tmp_path / "agent"
    install.mkdir()
    for name, payload in payloads.items():
        if name != "xgent_mcs.py":
            (install / name).write_bytes(payload)
    monkeypatch.setattr(guardian_module, "SCRIPT_DIR", install)
    guardian = object.__new__(guardian_module.Guardian)
    guardian.state = {"desired_running": True}

    assert guardian._restore_missing_agent_files() == ["xgent_mcs.py"]
    assert {name: (install / name).read_bytes() for name in payloads} == payloads
    assert not (install / ".env").exists()
    assert json.loads((recovery_root / "current.json").read_text())["slot"] == slot


@pytest.mark.parametrize("tamper", ["archive", "manifest", "unknown-key"])
def test_tampered_recovery_package_never_creates_worker_files(
    signed_recovery, tmp_path, tamper, monkeypatch
):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    slot = recovery.stage_recovery_package(manifest, archive, recovery_root)
    cached = recovery_root / "slots" / slot
    if tamper == "archive":
        cached_archive = cached / "XIDER-source.zip"
        cached_archive.chmod(0o600)
        cached_archive.write_bytes(cached_archive.read_bytes() + b"tampering")
    elif tamper == "manifest":
        cached_manifest = cached / "release-manifest.json"
        cached_manifest.chmod(0o600)
        value = json.loads(cached_manifest.read_text())
        value["release"] = "v4.1.1"
        cached_manifest.write_text(json.dumps(value), encoding="utf-8")
    else:
        import release_signature
        monkeypatch.setattr(release_signature, "TRUSTED_RELEASE_KEYS", {})
    install = tmp_path / "agent"

    with pytest.raises(ValueError):
        recovery.restore_missing_agent_files(install, recovery_root)

    assert not install.exists()


def test_explicit_stop_prevents_even_valid_package_recovery(
    guardian_module, signed_recovery, monkeypatch, tmp_path
):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)
    install = tmp_path / "agent"
    monkeypatch.setattr(guardian_module, "SCRIPT_DIR", install)
    guardian_module.STATE_FILE.write_text(json.dumps({"desired_running": False}), encoding="utf-8")
    guardian = object.__new__(guardian_module.Guardian)
    guardian.state = {"desired_running": True}
    spawn = Mock()
    monkeypatch.setattr(guardian_module.subprocess, "Popen", spawn)

    with pytest.raises(RuntimeError, match="явно остановлен"):
        guardian.start_agent()

    spawn.assert_not_called()
    assert not install.exists()
    assert guardian.state["desired_running"] is False


def test_stop_during_package_verification_prevents_restoration(signed_recovery, tmp_path):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)
    install = tmp_path / "agent"

    def stopped():
        raise RuntimeError("explicit owner stop")

    with pytest.raises(RuntimeError, match="explicit owner stop"):
        recovery.restore_missing_agent_files(install, recovery_root, before_restore=stopped)

    assert not install.exists()


def test_uninstall_removed_intent_file_prevents_restoration(
    guardian_module, signed_recovery, monkeypatch, tmp_path
):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)
    monkeypatch.setattr(guardian_module, "SCRIPT_DIR", tmp_path / "agent")
    guardian = object.__new__(guardian_module.Guardian)
    guardian.state = {"desired_running": True}
    guardian._intent_file_required = True

    with pytest.raises(RuntimeError, match="Состояние Guardian удалено"):
        guardian._restore_missing_agent_files()

    assert not (tmp_path / "agent").exists()


def test_recovery_activation_is_deferred_until_explicit_health_commit(signed_recovery, tmp_path):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    slot = recovery.stage_recovery_package(manifest, archive, recovery_root, activate=False)

    assert not (recovery_root / "current.json").exists()
    with pytest.raises(RuntimeError, match="ещё не установлен"):
        recovery.restore_missing_agent_files(tmp_path / "agent", recovery_root)
    assert recovery.activate_recovery_package(recovery_root, slot) == "v4.1.0"
    assert json.loads((recovery_root / "current.json").read_text())["slot"] == slot


def test_recovery_never_mixes_present_files_with_another_release(signed_recovery, tmp_path):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)
    install = tmp_path / "agent"
    install.mkdir()
    (install / "config.py").write_text("# another installed release\n", encoding="utf-8")

    with pytest.raises(ValueError, match="differs from the selected recovery release"):
        recovery.restore_missing_agent_files(install, recovery_root)

    assert [path.name for path in install.iterdir()] == ["config.py"]


def test_missing_worker_monitor_respects_stop_intent(guardian_module, monkeypatch):
    guardian = object.__new__(guardian_module.Guardian)
    guardian.state = {"auto_restart": True, "desired_running": True}
    guardian.lock = threading.RLock()
    guardian.stop_event = Mock()
    guardian.stop_event.wait.side_effect = [False, True]
    guardian_module.STATE_FILE.write_text(json.dumps({"desired_running": False}), encoding="utf-8")
    start = Mock()
    monkeypatch.setattr(guardian, "start_agent", start)

    guardian.monitor()

    start.assert_not_called()


def test_candidate_validation_requires_exact_complete_signed_source(signed_recovery, tmp_path):
    recovery, manifest, archive, recovery_root, payloads = signed_recovery
    slot = recovery.stage_recovery_package(manifest, archive, recovery_root, activate=False)
    install = tmp_path / "candidate"
    install.mkdir()
    for name, payload in payloads.items():
        (install / name).write_bytes(payload)

    assert recovery.validate_recovery_install(install, recovery_root, slot) == "v4.1.0"
    assert not (recovery_root / "current.json").exists()
    (install / "config.py").write_text("# changed source\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from the selected recovery release"):
        recovery.validate_recovery_install(install, recovery_root, slot)
    (install / "config.py").unlink()
    with pytest.raises(ValueError, match="not a regular file"):
        recovery.validate_recovery_install(install, recovery_root, slot)
    assert not (recovery_root / "current.json").exists()


def test_existing_recovery_slot_is_never_repaired_from_new_installer_inputs(signed_recovery):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    slot = recovery.stage_recovery_package(manifest, archive, recovery_root)
    cached = recovery_root / "slots" / slot / "XIDER-source.zip"
    cached.chmod(0o600)
    cached.write_bytes(b"damaged existing immutable slot")

    with pytest.raises(ValueError):
        recovery.stage_recovery_package(manifest, archive, recovery_root)

    assert cached.read_bytes() == b"damaged existing immutable slot"


def test_recovery_rejects_secrets_in_the_requested_file_allowlist(signed_recovery, tmp_path):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)

    with pytest.raises(ValueError, match="Invalid recovery file allowlist"):
        recovery.restore_missing_agent_files(tmp_path / "agent", recovery_root, (".env",))

    assert not (tmp_path / "agent").exists()


def test_recovery_selection_cannot_traverse_outside_its_slot(signed_recovery, tmp_path):
    recovery, manifest, archive, recovery_root, _payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)
    (recovery_root / "current.json").write_text(json.dumps({"schema": 1, "slot": "../../source"}), encoding="utf-8")

    with pytest.raises(ValueError, match="selection is invalid"):
        recovery.restore_missing_agent_files(tmp_path / "agent", recovery_root)

    assert not (tmp_path / "agent").exists()


@pytest.fixture
def pending_missing_worker(guardian_module, signed_recovery, monkeypatch, tmp_path):
    import update_package

    recovery, manifest, archive, recovery_root, old_payloads = signed_recovery
    recovery.stage_recovery_package(manifest, archive, recovery_root)
    install = tmp_path / "agent"
    candidate = tmp_path / "candidate"
    install.mkdir()
    candidate.mkdir()
    for name, payload in old_payloads.items():
        (install / name).write_bytes(payload)
        (candidate / name).write_bytes(payload if name == "requirements.txt" else b"# new pending source\n" + payload)
    journal = guardian_module.CONFIG_DIR / update_package.UPDATE_STATE_NAME
    update_package.install_agent_files(
        candidate, install, guardian_module.CONFIG_DIR / "agent-backups" / "pending",
        recovery.RECOVERY_FILES, transaction_path=journal,
        recovery_slot="v4.1.1-" + "d" * 64,
    )
    (install / "xgent_mcs.py").unlink()
    singleton_dir = tmp_path / "singleton"
    singleton_dir.mkdir()
    monkeypatch.setattr(guardian_module, "SCRIPT_DIR", install)
    monkeypatch.setattr(guardian_module.tempfile, "gettempdir", lambda: str(singleton_dir))
    fake_fcntl = Mock(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)
    monkeypatch.setattr(guardian_module, "fcntl", fake_fcntl)
    guardian = object.__new__(guardian_module.Guardian)
    guardian.state = {"desired_running": True}
    return guardian, install, journal, recovery_root, old_payloads, fake_fcntl


def test_missing_worker_with_pending_update_rolls_back_before_signed_gap_recovery(
    pending_missing_worker,
):
    guardian, install, journal, recovery_root, old_payloads, lock = pending_missing_worker
    previous_pointer = (recovery_root / "current.json").read_bytes()

    assert guardian._restore_missing_agent_files() == []

    assert {name: (install / name).read_bytes() for name in old_payloads} == old_payloads
    assert not journal.exists()
    assert (recovery_root / "current.json").read_bytes() == previous_pointer
    assert [call.args[1] for call in lock.flock.call_args_list] == [6, 8]


def test_missing_worker_journal_never_mutates_a_live_updater_lock(pending_missing_worker):
    guardian, install, journal, recovery_root, _old_payloads, lock = pending_missing_worker
    before = {path.name: path.read_bytes() for path in install.iterdir()}
    journal_before = journal.read_bytes()
    pointer_before = (recovery_root / "current.json").read_bytes()
    lock.flock.side_effect = BlockingIOError("live worker owns this lock")

    with pytest.raises(RuntimeError, match="удерживает singleton lock"):
        guardian._restore_missing_agent_files()

    assert {path.name: path.read_bytes() for path in install.iterdir()} == before
    assert journal.read_bytes() == journal_before
    assert (recovery_root / "current.json").read_bytes() == pointer_before
    assert len(lock.flock.call_args_list) == 1


def test_explicit_stop_prevents_pending_missing_worker_rollback(pending_missing_worker):
    guardian, install, journal, recovery_root, _old_payloads, lock = pending_missing_worker
    guardian.state["desired_running"] = False
    before = {path.name: path.read_bytes() for path in install.iterdir()}
    journal_before = journal.read_bytes()
    pointer_before = (recovery_root / "current.json").read_bytes()

    with pytest.raises(RuntimeError, match="явно остановлен"):
        guardian._restore_missing_agent_files()

    assert {path.name: path.read_bytes() for path in install.iterdir()} == before
    assert journal.read_bytes() == journal_before
    assert (recovery_root / "current.json").read_bytes() == pointer_before
    lock.flock.assert_not_called()


@pytest.mark.parametrize("tamper", ["backup-path", "status", "recovery-previous"])
def test_tampered_missing_worker_journal_blocks_all_source_writes(pending_missing_worker, tamper):
    guardian, install, journal, recovery_root, _old_payloads, _lock = pending_missing_worker
    state = json.loads(journal.read_text(encoding="utf-8"))
    if tamper == "backup-path":
        state["backup_dir"] = str(install.parent / "outside-backup")
    elif tamper == "status":
        state["status"] = "tampered"
    else:
        state["recovery_previous_slot"] = "../../outside-cache"
    journal.write_text(json.dumps(state), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in install.iterdir()}
    journal_before = journal.read_bytes()
    pointer_before = (recovery_root / "current.json").read_bytes()

    with pytest.raises(ValueError):
        guardian._restore_missing_agent_files()

    assert {path.name: path.read_bytes() for path in install.iterdir()} == before
    assert journal.read_bytes() == journal_before
    assert (recovery_root / "current.json").read_bytes() == pointer_before


def test_running_worker_files_are_never_rolled_back_by_guardian(
    pending_missing_worker, monkeypatch, guardian_module,
):
    guardian, install, journal, _recovery_root, old_payloads, lock = pending_missing_worker
    (install / "xgent_mcs.py").write_bytes(b"# still running pending worker\n")
    journal_before = journal.read_bytes()
    # All required source files exist, so Guardian should not inspect or arm
    # this journal. The live worker alone owns its MQTT-health transaction.
    assert set(path.name for path in install.iterdir()) == set(old_payloads)

    assert guardian._restore_missing_agent_files() == []

    assert journal.read_bytes() == journal_before
    lock.flock.assert_not_called()


def test_pending_missing_worker_fails_closed_without_flock(pending_missing_worker, monkeypatch, guardian_module):
    guardian, install, journal, _recovery_root, _old_payloads, _lock = pending_missing_worker
    monkeypatch.setattr(guardian_module, "fcntl", None)
    before = {path.name: path.read_bytes() for path in install.iterdir()}
    journal_before = journal.read_bytes()

    with pytest.raises(RuntimeError, match="singleton flock"):
        guardian._restore_missing_agent_files()

    assert {path.name: path.read_bytes() for path in install.iterdir()} == before
    assert journal.read_bytes() == journal_before


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
