import ctypes
from types import SimpleNamespace

from xg_commands import input as input_commands


def test_type_text_caps_payload_and_preserves_response():
    sent = []
    input_calls = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.args = (name, daemon)

        def start(self):
            self.target()

    input_commands.type_text(
        {"text": "x" * 700}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        ctypes_module=SimpleNamespace(
            Structure=ctypes.Structure, Union=ctypes.Union, c_ushort=ctypes.c_ushort,
            c_ulong=ctypes.c_ulong, c_ubyte=ctypes.c_ubyte, POINTER=ctypes.POINTER,
            sizeof=ctypes.sizeof,
            windll=SimpleNamespace(user32=SimpleNamespace(
                SendInput=lambda *args: input_calls.append(args) or 1,
            )),
        ), time_module=type("Time", (), {"sleep": staticmethod(lambda _seconds: None)}),
        threading_module=type("Threading", (), {"Thread": Thread}),
    )

    assert len(input_calls) == 500
    assert sent == [("type_text", {
        "type": "type_text", "device_id": "windows-1", "ok": True, "len": 500,
    })]
