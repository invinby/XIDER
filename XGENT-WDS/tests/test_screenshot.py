"""No live ImageGrab or Windows permission/session mutation."""
import base64
from types import SimpleNamespace
import threading
import builtins

import pytest
from PIL import Image, ImageGrab
import xgent_wds as agent


@pytest.mark.parametrize("case,state", [
    ("denied", "permission_denied"), ("session", "session_unavailable"),
    ("missing", "tool_missing"), ("command", "command_failed"),
    ("empty", "file_empty"), ("large", "file_oversized"),
    ("bad", "file_unreadable"), ("timeout", "timeout"),
    ("ok", "success"), ("transport", "transport_failed"),
    ("4k", "success"),
    ("transport_exception", "transport_failed"),
])
def test_capture_diagnostics(monkeypatch, case, state):
    sent = []
    client = object.__new__(agent.XgentClient)
    def publish(topic, value):
        sent.append((topic, value))
        if case == "transport_exception" and value.get("image"):
            raise RuntimeError("MQTT publish failed")
        return False if case == "transport" and value.get("image") else True
    client._publish_response = publish
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: case != "session", raising=False)
    real_import = builtins.__import__
    def importing(name, *args, **kwargs):
        if case == "missing" and name == "PIL":
            raise ImportError("Pillow missing")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    def capture(**kwargs):
        errors = {"denied": PermissionError("access denied"), "command": RuntimeError("capture failed"), "timeout": TimeoutError("timed out")}
        if case in errors:
            raise errors[case]
        if case == "bad":
            return None
        img = Image.new("RGB", (0, 0) if case == "empty" else (3840, 2160) if case == "4k" else (10, 10))
        if case == "large":
            img._size = (100000, 100000)
        return img
    monkeypatch.setattr(ImageGrab, "grab", capture)
    client._do_screenshot({})
    topic, value = sent[-1]
    assert topic == "screenshot"
    assert value.get("platform") == "Windows"
    assert value.get("state") == state
    assert value.get("detail")
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


def test_hung_capture_frees_command_worker_and_never_accumulates(monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()
    late_closed = threading.Event()
    captures = []
    sent = []
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, value: sent.append(value)
    monkeypatch.setattr(agent, "_screenshot_session_available", lambda: True)
    monkeypatch.setattr(agent, "SCREENSHOT_TIMEOUT", 0.01, raising=False)
    def capture(**kwargs):
        captures.append(1)
        entered.set()
        release.wait(2)
        img = Image.new("RGB", (10, 10))
        close = img.close
        def closed():
            close()
            late_closed.set()
        img.close = closed
        return img
    monkeypatch.setattr(ImageGrab, "grab", capture)
    def request():
        try: client._do_screenshot({})
        finally: finished.set()
    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert entered.wait(1)
        assert finished.wait(0.3), "native capture blocked the command worker"
        assert sent[-1]["state"] == "timeout"
        client._do_screenshot({})
        assert sent[-1]["state"] == "timeout"
        assert captures == [1]
    finally:
        release.set()
        thread.join(2)
    assert late_closed.wait(1), "late image leaked after caller timeout"
