from types import SimpleNamespace

from xg_commands import maintenance


def test_shell_cancel_normalizes_unknown_status_and_request_id():
    sent = []
    maintenance.shell_cancel(
        {"target_request_id": "x" * 129}, device_id="windows-1",
        load_component=lambda _name: SimpleNamespace(cancel_shell=lambda _target: "running"),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert sent == [("shell_cancel", {
        "type": "shell_cancel", "device_id": "windows-1",
        "target_request_id": "", "status": "unknown",
    })]
