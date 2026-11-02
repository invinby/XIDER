"""XIDER prank cancellation must only affect owned, reversible scenes."""

from types import SimpleNamespace
import threading
from unittest.mock import Mock

import xgent_mcs as mcs


def _client():
    client = object.__new__(mcs.XgentClient)
    client.responses = []
    client._publish_response = lambda topic, payload: client.responses.append((topic, payload))
    return client


def test_stop_all_cancels_registered_owned_scene_and_reports_partial(monkeypatch):
    client = _client()
    called = []
    client._register_prank_cancel("demo-1", lambda: called.append("demo-1"))
    monkeypatch.setattr(client, "_restore_saved_prank_wallpaper", lambda: (True, "wallpaper restored"))

    client._do_prank_stop_all({})

    assert called == ["demo-1"]
    assert len(client.responses) == 1
    topic, result = client.responses[0]
    assert topic == "output"
    assert result["type"] == "prank_stop_all"
    assert result["ok"] is False
    assert result["state"] == "partial"
    assert "afplay" not in result["text"]


def test_meme_wallpaper_saves_original_for_stop_all_restore(monkeypatch, tmp_path):
    client = _client()
    monkeypatch.setattr(mcs, "CONFIG_DIR", tmp_path)
    observations = iter(("/Users/owner/Original.jpg", "/Users/owner/FirstMeme.jpg", ""))
    monkeypatch.setattr(
        mcs.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=next(observations), stderr="",
        ),
    )
    set_calls = []
    monkeypatch.setattr(client, "_do_wallpaper_set", lambda payload: set_calls.append(payload) or True)

    client._do_prank_meme_wallpaper({})

    backup = tmp_path / "wallpaper_backup.json"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == '["/Users/owner/Original.jpg"]'
    assert set_calls == [{"random_meme": True}]
    assert client.responses[-1][1]["type"] == "prank_meme_wallpaper"
    assert client.responses[-1][1]["ok"] is True

    client._do_prank_meme_wallpaper({})
    assert backup.read_text(encoding="utf-8") == '["/Users/owner/Original.jpg"]'
    assert set_calls == [{"random_meme": True}, {"random_meme": True}]

    restored, detail = client._restore_saved_prank_wallpaper()
    assert restored is True
    assert "восстановлены" in detail
    assert not backup.exists()


def test_fake_update_registers_only_its_child_for_cancellation(monkeypatch):
    client = _client()
    child = Mock()
    release_wait = threading.Event()
    child.poll.return_value = None
    child.wait.side_effect = release_wait.wait
    monkeypatch.setattr(mcs.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(mcs.sys, "frozen", True, raising=False)

    client._do_prank_fake_update({})
    cancelled = client._cancel_active_pranks()
    release_wait.set()

    assert cancelled
    child.terminate.assert_called_once_with()


def test_slow_mouse_cancel_restores_exact_original_value(monkeypatch):
    client = _client()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["read", "-g"]:
            return SimpleNamespace(stdout="2.25\n", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", run)
    monkeypatch.setattr(mcs.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))

    client._do_prank_slow_mouse({})
    client._cancel_active_pranks()

    writes = [call[-1] for call in calls if call[1:3] == ["write", "-g"]]
    assert writes == ["0.1", "2.25"]


def test_slow_mouse_fails_closed_if_original_value_cannot_be_read(monkeypatch):
    client = _client()
    calls = []
    monkeypatch.setattr(
        mcs.subprocess,
        "run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(stdout="", stderr="missing", returncode=1),
    )

    client._do_prank_slow_mouse({})

    assert len(calls) == 1
    assert calls[0][1:3] == ["read", "-g"]
    assert client.responses[0][1]["ok"] is False
