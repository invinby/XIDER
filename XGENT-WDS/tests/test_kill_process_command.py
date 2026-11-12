from types import SimpleNamespace

from xg_commands import system_info


def test_kill_process_terminates_then_kills_after_timeout():
    calls = []
    sent = []

    class Process:
        def name(self):
            return "demo.exe"

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout):
            calls.append(("wait", timeout))
            raise TimeoutError

        def kill(self):
            calls.append("kill")

    system_info.kill_process(
        {"pid": "42"}, device_id="windows-1",
        publish_response=lambda topic, payload: sent.append((topic, payload)),
        psutil_module=SimpleNamespace(Process=lambda pid: (calls.append(("process", pid)) or Process()),
                                      TimeoutExpired=TimeoutError),
        logger=SimpleNamespace(info=lambda *args: calls.append(("log", *args))),
    )
    assert calls == [
        ("process", 42), ("log", "kill_process: pid=%s (%s)", 42, "demo.exe"),
        "terminate", ("wait", 3), "kill",
    ]
    assert sent == [("kill_process", {
        "type": "kill_process", "device_id": "windows-1", "pid": 42,
        "name": "demo.exe", "killed": True,
    })]
