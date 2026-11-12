from io import StringIO
from types import SimpleNamespace

from xg_commands import system_info


def test_installed_apps_keeps_registry_query_and_limits_output_to_40():
    captured, sent = [], []
    output = "\n".join(f"App {index:02d}" for index in range(45))
    system_info.installed_apps(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        run_without_console=lambda check, command, **kwargs: (
            captured.append((check, command, kwargs)) or output
        ),
        check_output="check_output", devnull="DEVNULL",
    )
    check, command, kwargs = captured[0]
    assert check == "check_output"
    assert command[:3] == ["powershell", "-NoProfile", "-Command"]
    assert "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*" in command[3]
    assert kwargs == {"text": True, "stderr": "DEVNULL", "timeout": 25}
    assert "Установленные программы (45)" in sent[0][1]["text"]
    assert "App 39" in sent[0][1]["text"] and "App 40" not in sent[0][1]["text"]
    assert "и ещё 5" in sent[0][1]["text"]


def test_history_command_uses_clamped_tail_and_expected_path():
    sent, opened = [], []
    path_module = SimpleNamespace(
        join=lambda *parts: "|".join(parts), exists=lambda path: True,
    )
    os_module = SimpleNamespace(environ={"APPDATA": "profile"}, path=path_module)

    def open_file(path, mode, **kwargs):
        opened.append((path, mode, kwargs))
        return StringIO("one\ntwo\nthree\n")

    system_info.history_cmd(
        {"lines": 2}, device_id="d", publish_response=lambda *args: sent.append(args),
        clamp=lambda value, minimum, maximum, default: max(minimum, min(maximum, int(value or default))),
        os_module=os_module, open_file=open_file,
    )
    assert opened == [(
        "profile|Microsoft\\Windows\\PowerShell\\PSReadLine\\ConsoleHost_history.txt",
        "r", {"encoding": "utf-8", "errors": "ignore"},
    )]
    assert sent[0][1]["text"] == "🕘 История PowerShell (последние):\n• two\n• three"
