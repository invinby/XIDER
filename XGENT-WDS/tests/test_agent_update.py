"""Windows source-agent update contract tests; no network, process or device is used."""

from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

import xgent_wds as wds


def _update_client():
    client = object.__new__(wds.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    return client, sent


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


def test_source_update_validates_and_installs_only_windows_allowlist(monkeypatch, tmp_path):
    client, sent = _update_client()
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    stage = tmp_path / "stage"
    stage.mkdir()
    installed = []
    threads = []
    downloaded = {}

    def download(manifest, archive, *, release_tag=None):
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

    def install(source, install_dir, backup_dir, files, *, transaction_path):
        installed.append({
            "source": Path(source),
            "install_dir": Path(install_dir),
            "backup_dir": Path(backup_dir),
            "files": tuple(files),
            "transaction_path": Path(transaction_path),
        })
        return tmp_path / "agent-backups" / "previous"

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            threads.append((target, name, daemon))

        def start(self):
            return None

    monkeypatch.setattr(wds, "download_verified_source_archive", download)
    monkeypatch.setattr(wds, "extract_agent_files", extract)
    monkeypatch.setattr(wds, "install_agent_files", install)
    monkeypatch.setattr(wds.threading, "Thread", DeferredThread)

    client._do_agent_update({"update": True, "release_tag": "v4.0.1"})

    assert downloaded == {
        "manifest": "release-manifest.json",
        "archive": "XIDER-source.zip",
        "release_tag": "v4.0.1",
    }
    assert len(installed) == 1
    assert installed[0]["files"] == (
        "xgent_wds.py", "config.py", "crypto.py", "xgencrypto.py",
        "release_signature.py", "update_package.py",
    )
    assert installed[0]["install_dir"] == Path(wds.__file__).resolve().parent
    assert installed[0]["transaction_path"] == tmp_path / wds.UPDATE_STATE_NAME
    assert len(threads) == 1 and threads[0][1:] == ("xgent-wds-update-restart", True)
    assert sent[0][1]["ok"] is True
    assert sent[0][1]["state"] == "restarting"
    assert sent[0][1]["health_check"] == "pending"
    assert sent[0][1]["release_tag"] == "v4.0.1"


def test_source_update_stops_before_extraction_when_release_verification_fails(monkeypatch, tmp_path):
    client, sent = _update_client()
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
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

    client._do_agent_update({"update": True})

    assert sent[0][1]["ok"] is False
    assert sent[0][1]["state"] == "failed"
    assert "signature rejected" in sent[0][1]["text"]
    assert "не подтверждено" in sent[0][1]["text"]
    assert not (tmp_path / "agent-backups").exists()


def test_frozen_windows_update_fails_closed_before_download(monkeypatch, tmp_path):
    client, sent = _update_client()
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        wds,
        "download_verified_source_archive",
        lambda *_args, **_kwargs: pytest.fail("frozen EXE must fail before download"),
    )

    client._do_agent_update({"update": True})

    assert sent[0][1]["ok"] is False
    assert sent[0][1]["state"] == "unsupported_package"
    assert "ничего не менял" in sent[0][1]["text"].lower()
    assert list(tmp_path.iterdir()) == []


def test_mqtt_connect_confirms_pending_windows_update(monkeypatch, tmp_path):
    client = object.__new__(wds.XgentClient)
    client._update_health = threading.Event()
    client._publish_status = lambda: None
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wds.sys, "frozen", False, raising=False)
    monkeypatch.setattr(wds, "mark_agent_update_healthy", lambda *_args: True)
    mqtt_client = MagicMock()

    client._on_connect(mqtt_client, None, None, 0)

    assert mqtt_client.subscribe.call_count == 2
    assert client._update_health.is_set()
