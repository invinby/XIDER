from types import SimpleNamespace

from xg_commands import input as input_commands


def test_hotkey_presses_keys_and_publishes_success():
    events = []
    sent = []

    class Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    user32 = SimpleNamespace(keybd_event=lambda *args: events.append(args))
    input_commands.hotkey(
        {"keys": "ctrl+a"}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        map_hotkeys=lambda spec: [17, 65] if spec == "ctrl+a" else [],
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=user32)),
        threading_module=SimpleNamespace(Thread=Thread),
    )

    assert events == [(17, 0, 0, 0), (65, 0, 0, 0), (65, 0, 2, 0), (17, 0, 2, 0)]
    assert sent == [("hotkey", {"type": "hotkey", "device_id": "windows-1",
                              "ok": True, "keys": "ctrl+a"})]


def test_hotkey_returns_validation_error_without_starting_thread():
    sent = []

    class Thread:
        def __init__(self, **_kwargs):
            raise AssertionError("invalid hotkey must not start a thread")

    def invalid(_spec):
        raise ValueError("unsupported key")

    input_commands.hotkey(
        {"keys": "bad"}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        map_hotkeys=invalid, ctypes_module=None,
        threading_module=SimpleNamespace(Thread=Thread),
    )

    assert sent == [("hotkey", {"type": "hotkey", "device_id": "windows-1",
                              "ok": False, "error": "unsupported key"})]
