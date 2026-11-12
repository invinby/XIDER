from types import SimpleNamespace

from xg_commands import input as input_commands


def test_clipboard_set_copies_text_and_publishes_length():
    sent = []
    logs = []
    copied = []
    input_commands.clipboard_set(
        {"text": 123}, device_id="windows-1",
        publish_response=lambda topic, payload: sent.append((topic, payload)),
        logger=SimpleNamespace(info=lambda *args: logs.append(args)),
        pyperclip_module=SimpleNamespace(copy=copied.append),
    )
    assert copied == ["123"]
    assert sent == [("clipboard_set", {
        "type": "clipboard_set", "device_id": "windows-1", "length": 3,
    })]
    assert logs == [("В буфер обмена записано %d символов", 3)]
