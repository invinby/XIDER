import shlex
import subprocess
import sys
import threading
import time

import shell_execution


def _python_sleep_command():
    code = "import time; time.sleep(30)"
    if sys.platform == "win32":
        return subprocess.list2cmdline([sys.executable, "-c", code])
    return " ".join(shlex.quote(part) for part in (sys.executable, "-c", code))


def test_cancel_shell_stops_only_matching_active_request_and_reports_confirmation():
    request_id = "cancel-test-1"
    result_box = {}
    worker = threading.Thread(
        target=lambda: result_box.setdefault(
            "result", shell_execution.execute_shell(_python_sleep_command(), 18, request_id=request_id)
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 5
    while request_id not in shell_execution.active_shell_requests() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert request_id in shell_execution.active_shell_requests()
    assert shell_execution.cancel_shell("different-request") == "unknown"
    assert worker.is_alive()
    assert shell_execution.cancel_shell(request_id) == "cancelled"
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert result_box["result"]["status"] == "cancelled"
    assert request_id not in shell_execution.active_shell_requests()
