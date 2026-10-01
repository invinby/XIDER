"""Agent update dispatch contract tests; no GitHub or device files are changed."""

import json
from pathlib import Path
import threading
from unittest.mock import MagicMock

import xgent_mcs as mcs
import update_package as package


def test_agent_update_passes_release_tag_to_verifier_and_fails_closed(monkeypatch, tmp_path):
    client = object.__new__(mcs.XgentClient)
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
    client = object.__new__(mcs.XgentClient)
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
    client = object.__new__(mcs.XgentClient)
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
    client = object.__new__(mcs.XgentClient)
    client._client = MagicMock()
    monkeypatch.setattr(mcs, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(mcs.sys, "frozen", False, raising=False)
    monkeypatch.setattr(mcs, "trusted_release_keys_ready", lambda: True)

    client._publish_status()

    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = mcs.verify_message(envelope)
    assert payload["release_update_ready"] is True


def test_frozen_agent_update_fails_closed_without_mutating_bundle(monkeypatch, tmp_path):
    client = object.__new__(mcs.XgentClient)
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


def test_mqtt_connection_commits_pending_update_health(monkeypatch, tmp_path):
    client = object.__new__(mcs.XgentClient)
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

    client._on_connect(mqtt_client, None, None, 0)

    assert client._update_health.is_set()
    assert not state_path.exists()
    assert mqtt_client.subscribe.call_count == 2
