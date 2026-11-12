from types import SimpleNamespace

from xg_commands import system_info


def test_proc_kill_name_preserves_result_and_response_envelope():
    sent = []
    logs = []
    result = {"ok": False, "state": "not_found", "count": 0, "killed": [], "errors": 0}
    system_info.proc_kill_name(
        {"name": "editor"}, device_id="windows-1",
        publish_response=lambda topic, payload: sent.append((topic, payload)),
        kill_by_name=lambda name: result if name == "editor" else None,
        logger=SimpleNamespace(info=lambda *args: logs.append(args)),
    )
    assert logs == [("proc_kill_name: %s", result)]
    assert sent == [("proc_kill_name", {
        "type": "proc_kill_name", "device_id": "windows-1", **result,
    })]
