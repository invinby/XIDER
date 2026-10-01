"""The macOS fake-update prank launches a local, bounded demo only."""

import sys
from pathlib import Path
from unittest.mock import Mock

import xgent_mcs as mcs


def _client_with_response_sink():
    client = object.__new__(mcs.XgentClient)
    client.responses = []
    client._publish_response = lambda topic, payload: client.responses.append((topic, payload))
    return client


def test_fake_update_launches_local_demo_in_source_mode(monkeypatch):
    client = _client_with_response_sink()
    child = Mock()
    child.poll.return_value = None
    popen = Mock(return_value=child)
    monkeypatch.setattr(mcs.subprocess, "Popen", popen)
    monkeypatch.setattr(mcs.sys, "frozen", False, raising=False)

    client._do_prank_fake_update({})

    command = popen.call_args.args[0]
    assert command == [sys.executable, str(Path(mcs.__file__).resolve()), "--xider-fake-update"]
    assert popen.call_args.kwargs == {"close_fds": True}
    assert client.responses[0][1]["ok"] is True
    assert "локальный полноэкранный демо-экран" in client.responses[0][1]["text"]
    assert "fakeupdate.net" not in repr(command)


def test_fake_update_reenters_frozen_binary_without_a_script_path(monkeypatch):
    client = _client_with_response_sink()
    popen = Mock(return_value=Mock(poll=Mock(return_value=None)))
    monkeypatch.setattr(mcs.subprocess, "Popen", popen)
    monkeypatch.setattr(mcs.sys, "frozen", True, raising=False)

    client._do_prank_fake_update({})

    assert popen.call_args.args[0] == [sys.executable, "--xider-fake-update"]
    assert client.responses[0][1]["ok"] is True


def test_fake_update_reports_child_start_failure(monkeypatch):
    client = _client_with_response_sink()
    popen = Mock(return_value=Mock(poll=Mock(return_value=1)))
    monkeypatch.setattr(mcs.subprocess, "Popen", popen)

    client._do_prank_fake_update({})

    assert client.responses[0][1]["ok"] is False
    assert "сразу завершился" in client.responses[0][1]["text"]


def test_demo_source_is_bounded_and_discloses_its_demo_state():
    source = (Path(mcs.__file__).with_name("fake_update_screen.py")).read_text(encoding="utf-8")

    assert "DEMO_SECONDS = 30.0" in source
    assert "ДЕМО" in source
    assert "Закрыть демо" in source
    assert "keyCode()) == 53" in source
    assert "setIndeterminate_(True)" in source
    assert "fakeupdate.net" not in source
