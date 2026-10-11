"""Capture is always mocked; no TCC prompts or live screen reads."""
import base64
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from PIL import Image
import xgent_mcs as agent


def test_capture_uses_system_binary_when_missing_from_path(monkeypatch, tmp_path):
    sent, invoked = [], []
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, value: sent.append(value) or True
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    original_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path.as_posix() == "/usr/sbin/screencapture" or original_is_file(path))
    assert agent._command_available("screencapture", ("/usr/sbin/screencapture",))
    original_temp = agent.tempfile.NamedTemporaryFile
    monkeypatch.setattr(agent.tempfile, "NamedTemporaryFile", lambda **kwargs: original_temp(dir=tmp_path, **kwargs))
    def capture(args, **kwargs):
        invoked.append(args)
        Image.new("RGB", (10, 10), "red").save(args[-1])
        return SimpleNamespace(returncode=0, stderr=b"")
    monkeypatch.setattr(agent.subprocess, "run", capture)
    client._do_screenshot({})
    assert sent[-1]["state"] == "success"
    assert invoked[0][:2] == ["/usr/sbin/screencapture", "-x"]
    assert not Path(invoked[0][-1]).exists()


@pytest.mark.parametrize("case,state", [
    ("denied", "permission_denied"), ("session", "session_unavailable"),
    ("missing", "tool_missing"), ("exit", "command_failed"),
    ("empty", "file_empty"), ("large", "file_oversized"),
    ("bad", "file_unreadable"), ("timeout", "timeout"),
    ("ok", "success"), ("transport", "transport_failed"),
    ("disappeared_tool", "tool_missing"),
    ("transport_exception", "transport_failed"),
])
def test_capture_diagnostics_and_cleanup(monkeypatch, tmp_path, case, state):
    paths = []
    sent = []
    client = object.__new__(agent.XgentClient)
    def publish(topic, value):
        sent.append((topic, value))
        if case == "transport_exception" and value.get("image"):
            raise RuntimeError("MQTT publish failed")
        return False if case == "transport" and value.get("image") else True
    client._publish_response = publish
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: case != "session", raising=False)
    monkeypatch.setattr(agent.shutil, "which", lambda name: None if case == "missing" else name)
    if case == "missing":
        original_is_file = Path.is_file
        monkeypatch.setattr(Path, "is_file", lambda path: False if path.as_posix() == "/usr/sbin/screencapture" else original_is_file(path))
    original_temp = agent.tempfile.NamedTemporaryFile
    def temporary(**kwargs):
        file = original_temp(dir=tmp_path, **kwargs)
        paths.append(Path(file.name))
        return file
    monkeypatch.setattr(agent.tempfile, "NamedTemporaryFile", temporary)
    def capture(args, **kwargs):
        path = Path(args[-1])
        if case == "disappeared_tool":
            raise FileNotFoundError("screencapture disappeared")
        if case == "timeout":
            raise agent.subprocess.TimeoutExpired(args, 10)
        if case in {"denied", "exit"}:
            return SimpleNamespace(returncode=1, stderr=b"Screen Recording permission denied" if case == "denied" else b"capture failed")
        if case == "large":
            with path.open("wb") as stream:
                stream.truncate(30 * 1024 * 1024 + 1)
        elif case == "bad":
            path.write_bytes(b"broken")
        elif case != "empty":
            Image.new("RGB", (10, 10), "red").save(path)
        return SimpleNamespace(returncode=0, stderr=b"")
    monkeypatch.setattr(agent.subprocess, "run", capture)
    client._do_screenshot({})
    topic, value = sent[-1]
    assert topic == "screenshot"
    assert value.get("platform") == "macOS"
    assert value.get("state") == state
    assert value.get("detail")
    assert all(not path.exists() for path in paths)
    if state == "success":
        assert base64.b64decode(value["image"], validate=True).startswith(b"\xff\xd8")
    else:
        assert not value.get("image")


@pytest.mark.parametrize("failure", ["rc", "wait", "unconfirmed"])
def test_publisher_exposes_positive_delivery_failure(failure):
    client = object.__new__(agent.XgentClient)
    client._command_context = threading.local()
    class Info:
        rc = 1 if failure == "rc" else 0
        def wait_for_publish(self, timeout):
            if failure == "wait":
                raise RuntimeError("delivery failed")
        def is_published(self):
            return failure != "unconfirmed"
    client._client = SimpleNamespace(publish=lambda *args, **kwargs: Info())
    assert client._publish_response("screenshot", {"state": "success", "detail": "captured"}) is False


@pytest.mark.parametrize("owner,current,available", [(1000, 1000, True), (1001, 1000, False), (0, 0, False)])
def test_console_owner_is_only_a_session_gate(monkeypatch, owner, current, available):
    monkeypatch.setattr(agent.os, "getuid", lambda: current, raising=False)
    monkeypatch.setattr(agent.os, "stat", lambda path: SimpleNamespace(st_uid=owner))
    assert agent._screenshot_session_available() is available
