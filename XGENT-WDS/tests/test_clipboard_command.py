from types import SimpleNamespace

from xg_commands import input as input_commands


def test_clipboard_reads_and_publishes_text():
    sent = []
    logs = []
    input_commands.clipboard(
        {}, device_id="windows-1",
        publish_response=lambda topic, payload: sent.append((topic, payload)),
        logger=SimpleNamespace(info=lambda *args: logs.append(args)),
        pyperclip_module=SimpleNamespace(paste=lambda: "hello"),
    )
    assert sent == [("clipboard", {"type": "clipboard", "device_id": "windows-1", "text": "hello"})]
    assert logs == [("Буфер обмена отправлен (%d символов)", 5)]
