"""Windows source-agent update contract tests; no network, process or device is used."""

from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest
import shutil
import zipfile

import xgent_wds as wds


def _update_client():
    client = object.__new__(wds.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload)) if topic == "output" else None
    return client, sent


def test_missing_manifest_pin_fails_before_download(monkeypatch):
    client, sent = _update_client()
    monkeypatch.setattr(wds.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(wds, 'download_verified_source_archive', lambda *a, **kw: pytest.fail('unbound download'))
    client._do_agent_update({'update': True, 'id': 'missing-pin'})
    assert sent[-1][1]['ok'] is False
    assert sent[-1][1]['state'] == 'manifest_pin_required'


def test_updated_approval_helpers_are_loaded_from_transaction_and_rollback_restores_fallback(monkeypatch, tmp_path):
    import update_package as package

    checkout = Path(wds.__file__).resolve().parent.parent
    windows = tmp_path / "repo" / "XGENT-WDS"
    sibling = windows.parent / "XGENT-MCS"
    stage = tmp_path / "stage"
    windows.mkdir(parents=True)
    sibling.mkdir()
    stage.mkdir()
    shutil.copy2(checkout / "XGENT-MCS" / "command_module_loader.py", sibling / "command_module_loader.py")
    (sibling / "shell_execution.py").write_text("RELEASE = 'previous'\n", encoding="utf-8")
    shutil.copy2(sibling / "command_module_loader.py", stage / "command_module_loader.py")
    (stage / "shell_execution.py").write_text("RELEASE = 'selected'\n", encoding="utf-8")
    monkeypatch.setattr(wds, "__file__", str(windows / "xgent_wds.py"))
    monkeypatch.setattr(wds, "_COMMAND_MODULE_LOADER", None)
    assert wds._load_command_approval_component("shell_execution").RELEASE == "previous"
    state = tmp_path / package.UPDATE_STATE_NAME
    package.install_agent_files(stage, windows, tmp_path / package.UPDATE_BACKUP_DIR / "previous",
                                ("command_module_loader.py", "shell_execution.py"), transaction_path=state)
    monkeypatch.setattr(wds, "_COMMAND_MODULE_LOADER", None)  # replacement process
    selected = wds._load_command_approval_component("shell_execution")
    assert selected.RELEASE == "selected"
    assert Path(selected.__file__).parent == windows
    package.rollback_unhealthy_agent(state, windows)
    monkeypatch.setattr(wds, "_COMMAND_MODULE_LOADER", None)  # rolled-back process
    assert wds._load_command_approval_component("shell_execution").RELEASE == "previous"
    assert not state.exists()


def test_source_import_searches_staged_windows_modules_before_mcs_fallback():
    windows_dir = Path(wds.__file__).resolve().parent
    mcs_dir = windows_dir.parent / "XGENT-MCS"

    assert str(windows_dir) in wds.sys.path
    assert str(mcs_dir) in wds.sys.path
    assert wds.sys.path.index(str(windows_dir)) < wds.sys.path.index(str(mcs_dir))


def test_early_bootstrap_lock_is_reused_by_agent_main(monkeypatch, tmp_path):
    import msvcrt

    monkeypatch.setattr(wds.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(wds, "_bootstrap_lock_file", None)
    monkeypatch.setattr(wds, "_instance_lock_file", None)

    assert wds._claim_bootstrap_lock() is True
    assert wds.acquire_instance_lock() is True
    try:
        handle = wds._bootstrap_lock_file
        assert handle is wds._instance_lock_file
        assert wds._claim_bootstrap_lock() is False
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
        wds._bootstrap_lock_file = None
        wds._instance_lock_file = None


@pytest.mark.parametrize("thread_start_failure", [False, True])
def test_source_update_validates_and_installs_only_windows_allowlist(monkeypatch, tmp_path, thread_start_failure):
    client, sent = _update_client()
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    stage = tmp_path / "stage"
    stage.mkdir()
    installed = []
    threads = []
    downloaded = {}
    progress = []
    rollbacks = []
    client._publish_response = lambda topic, payload: (sent if topic == "output" else progress).append((topic, payload))

    def download(manifest, archive, *, release_tag=None, expected_manifest_digest=None):
        assert expected_manifest_digest == 'a' * 64
        downloaded.update(
            manifest=Path(manifest).name,
            archive=Path(archive).name,
            release_tag=release_tag,
        )
        Path(manifest).write_text("signed manifest fixture", encoding="utf-8")
        Path(archive).write_bytes(b"verified archive fixture")
        return "v4.0.1"

    def extract(archive, destination, files, *, component_dir):
        assert Path(archive).read_bytes() == b"verified archive fixture"
        assert component_dir == "XGENT-WDS"
        for name in files:
            (stage / name).write_text("VALUE = 1\n", encoding="utf-8")
        return stage

    def install(source, install_dir, backup_dir, files, *, transaction_path, operation_id, release_tag, manifest_digest):
        assert manifest_digest == 'a' * 64
        installed.append({
            "source": Path(source),
            "install_dir": Path(install_dir),
            "backup_dir": Path(backup_dir),
            "files": tuple(files),
            "transaction_path": Path(transaction_path),
            "operation_id": operation_id, "release_tag": release_tag,
        })
        return tmp_path / "agent-backups" / "previous"

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            threads.append((target, name, daemon))

        def start(self):
            if thread_start_failure:
                raise OSError("thread creation denied")
            return None

    monkeypatch.setattr(wds, "download_verified_source_archive", download)
    monkeypatch.setattr(wds, "extract_agent_files", extract)
    monkeypatch.setattr(wds, "install_agent_files", install)
    monkeypatch.setattr(wds.threading, "Thread", DeferredThread)
    monkeypatch.setattr(wds, "rollback_unhealthy_agent", lambda *args: rollbacks.append(args) or {"restored": ["xgent_wds.py"], "removed": []})

    client._do_agent_update({"manifest_digest": "a" * 64, "update": True, "release_tag": "v4.0.1", "id": "bot-op-windows"})

    assert downloaded == {
        "manifest": "release-manifest.json",
        "archive": "XIDER-source.zip",
        "release_tag": "v4.0.1",
    }
    assert len(installed) == 1
    assert installed[0]["files"] == (
        "xgent_wds.py", "config.py", "crypto.py", "xgencrypto.py",
        "release_signature.py", "update_package.py",
        "command_module_loader.py", "command_signing.py", "command_replay.py", "shell_execution.py",
    )
    assert installed[0]["install_dir"] == Path(wds.__file__).resolve().parent
    assert installed[0]["transaction_path"] == tmp_path / wds.UPDATE_STATE_NAME
    assert len(threads) == 1 and threads[0][1:] == ("xgent-wds-update-restart", True)
    if thread_start_failure:
        assert rollbacks
        assert sent[-1][1]["update_state"] == "rolled_back"
        assert "thread creation denied" in sent[-1][1]["text"]
        assert sent[-1][1]["operation_id"] == "bot-op-windows"
        return
    assert sent[0][1]["ok"] is True
    assert sent[0][1]["state"] == "restarting"
    assert sent[0][1]["health_check"] == "pending"
    assert sent[0][1]["release_tag"] == "v4.0.1"
    assert installed[0]["operation_id"] == "bot-op-windows"
    assert sent[0][1]["operation_id"] == "bot-op-windows"
    assert sent[0][1]["update_state"] == "reconnecting"
    assert [item[1]["update_state"] for item in progress] == ["queued", "downloading", "verifying", "installing"]
    assert {item[1]["operation_id"] for item in progress} == {"bot-op-windows"}

    monkeypatch.setattr(wds.time, "sleep", lambda *_: None)
    monkeypatch.setattr(wds.os, "execv", lambda *_: (_ for _ in ()).throw(OSError("restart denied")))
    monkeypatch.setattr(wds, "rollback_unhealthy_agent", lambda *_: (_ for _ in ()).throw(OSError("restore denied")))
    threads[0][0]()
    assert sent[-1][1]["state"] == "failed"
    assert sent[-1][1]["update_state"] == "failed"
    assert sent[-1][1]["operation_id"] == "bot-op-windows"
    assert sent[-1][1]["id"] == "bot-op-windows"
    assert "restart denied" in sent[-1][1]["text"] and "restore denied" in sent[-1][1]["text"]


def test_source_update_stops_before_extraction_when_release_verification_fails(monkeypatch, tmp_path):
    client, sent = _update_client()
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    monkeypatch.setattr(wds, "read_agent_update_result", lambda *_: {
        "operation_id": "unrelated-old-operation", "update_state": "rolled_back",
    })
    monkeypatch.setattr(
        wds,
        "download_verified_source_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("signature rejected")),
    )
    monkeypatch.setattr(
        wds,
        "extract_agent_files",
        lambda *_args, **_kwargs: pytest.fail("must not extract an unverified archive"),
    )
    monkeypatch.setattr(
        wds,
        "install_agent_files",
        lambda *_args, **_kwargs: pytest.fail("must not modify files after signature failure"),
    )

    client._do_agent_update({"manifest_digest": "a" * 64, "update": True})

    assert sent[0][1]["ok"] is False
    assert sent[0][1]["state"] == "failed"
    assert sent[0][1]["update_state"] == "failed"
    assert "signature rejected" in sent[0][1]["text"]
    assert "не подтверждено" in sent[0][1]["text"]
    assert not (tmp_path / "agent-backups").exists()


@pytest.mark.parametrize("partial_restore", [False, True])
def test_windows_install_failure_reports_actual_matching_rollback_receipt(monkeypatch, tmp_path, partial_restore):
    import update_package as package
    install = tmp_path / "install"
    install.mkdir()
    files = ("xgent_wds.py", "config.py", "crypto.py", "xgencrypto.py",
             "release_signature.py", "update_package.py", "command_module_loader.py",
             "command_signing.py", "command_replay.py", "shell_execution.py")
    for name in files:
        (install / name).write_text("# previous\n", encoding="utf-8")
    (install / ".env").write_text("private fixture", encoding="utf-8")
    def download(manifest, archive, *, release_tag=None, expected_manifest_digest=None):
        Path(manifest).write_text("fixture", encoding="utf-8")
        with zipfile.ZipFile(archive, "w") as bundle:
            for name in files:
                component = "XGENT-WDS" if name in files[:4] else "XGENT-MCS"
                bundle.writestr(f"XIDER/{component}/{name}", "# replacement\n")
        return "v4.2.0"
    client, sent = _update_client()
    monkeypatch.setattr(wds, "__file__", str(install / "xgent_wds.py"))
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds, "download_verified_source_archive", download)
    original = package._atomic_copy
    def fail_install_and_optional_restore(src, dst):
        if Path(src).parent.name == "agent-stage" and Path(src).name == "config.py":
            raise OSError("install rejected")
        if partial_restore and package.UPDATE_BACKUP_DIR in Path(src).parts and Path(src).name == "config.py":
            raise OSError("restore rejected")
        return original(src, dst)
    monkeypatch.setattr(package, "_atomic_copy", fail_install_and_optional_restore)
    client._do_agent_update({"manifest_digest": "a" * 64, "update": True, "release_tag": "v4.2.0", "id": "windows-install-failure"})
    receipt = package.read_agent_update_result(tmp_path / package.UPDATE_STATE_NAME)
    expected = "failed" if partial_restore else "rolled_back"
    assert receipt["operation_id"] == "windows-install-failure"
    assert receipt["update_state"] == expected
    assert sent[-1][1]["update_state"] == receipt["update_state"]
    assert sent[-1][1]["operation_id"] == receipt["operation_id"]
    assert (install / ".env").read_text(encoding="utf-8") == "private fixture"


@pytest.mark.parametrize("incoming", [None, "", 123, "x" * 129, "valid-incoming"])
def test_frozen_rejection_always_has_bounded_operation_id(monkeypatch, incoming):
    client, sent = _update_client()
    monkeypatch.setattr(wds.sys, "frozen", True, raising=False)
    client._do_agent_update({"manifest_digest": "a" * 64, "update": True, "id": incoming})
    result = sent[-1][1]
    assert isinstance(result["operation_id"], str) and 1 <= len(result["operation_id"]) <= 128
    assert result["state"] == "unsupported_package" and result["update_state"] == "failed"
    if incoming == "valid-incoming":
        assert result["operation_id"] == incoming


@pytest.mark.parametrize("context_id", [None, "valid-context"])
def test_frozen_rejection_without_id_does_not_use_source_updater(monkeypatch, context_id):
    client, sent = _update_client()
    client._command_context = threading.local()
    client._command_context.cmd_id = context_id
    monkeypatch.setattr(wds.sys, "frozen", True, raising=False)
    monkeypatch.delattr(wds, "update_operation_id")
    client._do_agent_update({"manifest_digest": "a" * 64, "update": True})
    result = sent[-1][1]
    assert isinstance(result["operation_id"], str) and 1 <= len(result["operation_id"]) <= 128
    if context_id:
        assert result["operation_id"] == context_id


def test_frozen_windows_update_fails_closed_before_download(monkeypatch, tmp_path):
    client, sent = _update_client()
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        wds,
        "download_verified_source_archive",
        lambda *_args, **_kwargs: pytest.fail("frozen EXE must fail before download"),
    )

    client._do_agent_update({"manifest_digest": "a" * 64, "update": True})

    assert sent[0][1]["ok"] is False
    assert sent[0][1]["state"] == "unsupported_package"
    assert "ничего не менял" in sent[0][1]["text"].lower()
    assert list(tmp_path.iterdir()) == []


def test_mqtt_connect_confirms_pending_windows_update(monkeypatch, tmp_path):
    import update_package as package
    install, source = tmp_path / "install", tmp_path / "stage"
    install.mkdir()
    source.mkdir()
    (install / "xgent_wds.py").write_text("previous", encoding="utf-8")
    (source / "xgent_wds.py").write_text("replacement", encoding="utf-8")
    state = tmp_path / package.UPDATE_STATE_NAME
    package.install_agent_files(source, install, tmp_path / package.UPDATE_BACKUP_DIR / "previous",
                                ("xgent_wds.py",), transaction_path=state,
                                operation_id="bot-op-windows-health", release_tag="v4.0.1", manifest_digest='a' * 64)
    package.begin_agent_start(state, install)
    client = object.__new__(wds.XgentClient)
    client._update_health = threading.Event()
    client._subscription_lock = threading.RLock()
    client._running = threading.Event()
    client._running.set()
    client._pending_command_subacks = set()
    client._command_subscriptions_failed = False
    client._publish_status = lambda: None
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    monkeypatch.setattr(wds, "__file__", str(install / "xgent_wds.py"))
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(0, 101), (0, 102)]

    client._on_connect(mqtt_client, None, None, 0)

    assert mqtt_client.subscribe.call_count == 2
    assert not client._update_health.is_set()
    assert package.read_agent_update_result(state)["update_state"] == "health_check"
    client._on_subscribe(mqtt_client, None, 101, [0])
    assert not client._update_health.is_set()
    assert state.exists()
    client._on_subscribe(mqtt_client, None, 102, [0])
    assert client._update_health.is_set()
    assert not state.exists()
    assert package.read_agent_update_result(state)["operation_id"] == "bot-op-windows-health"
    assert package.read_agent_update_result(state)["update_state"] == "succeeded"
    assert package.read_agent_update_result(state)["manifest_digest"] == 'a' * 64


@pytest.mark.parametrize("failure", ["rejected", "send_failed", "disconnected"])
def test_windows_subscription_failure_cannot_commit_health(monkeypatch, tmp_path, failure):
    client = object.__new__(wds.XgentClient)
    client._update_health = threading.Event()
    client._subscription_lock = threading.RLock()
    client._running = threading.Event()
    client._running.set()
    client._pending_command_subacks = set()
    client._command_subscriptions_failed = False
    client._publish_status = lambda: None
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds, "mark_agent_update_healthy", lambda *_: pytest.fail("failed subscription must not commit"))
    mqtt_client = MagicMock()
    mqtt_client.subscribe.side_effect = [(0, 101), (1 if failure == "send_failed" else 0, 102)]
    client._on_connect(mqtt_client, None, None, 0)
    if failure == "disconnected":
        client._on_disconnect(mqtt_client, None, None, 1)
    client._on_subscribe(mqtt_client, None, 101, [0])
    client._on_subscribe(mqtt_client, None, 102, [128 if failure == "rejected" else 0])
    assert not client._update_health.is_set()
