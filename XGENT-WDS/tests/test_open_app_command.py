import logging
from types import SimpleNamespace

from xg_commands import desktop


def test_open_app_uses_startfile_first():
    calls = []
    desktop.open_app(
        {"app": "  calc.exe  "}, startfile=calls.append,
        subprocess_module=SimpleNamespace(Popen=lambda *args, **kwargs: calls.append((args, kwargs))),
        logger=logging.getLogger("test.open-app"),
    )
    assert calls == ["calc.exe"]


def test_open_app_falls_back_to_popen_without_shell():
    calls = []

    def missing(_app):
        raise FileNotFoundError("startfile unavailable")

    subprocess_module = SimpleNamespace(
        CREATE_NO_WINDOW=0x08000000,
        Popen=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    desktop.open_app(
        {"app": "calc.exe"}, startfile=missing,
        subprocess_module=subprocess_module, logger=logging.getLogger("test.open-app"),
    )
    assert calls == [(('calc.exe',), {"shell": False, "creationflags": 0x08000000})]


def test_open_app_ignores_empty_name():
    called = []
    desktop.open_app(
        {"app": "   "}, startfile=called.append,
        subprocess_module=SimpleNamespace(Popen=lambda *args, **kwargs: called.append(args)),
        logger=logging.getLogger("test.open-app"),
    )
    assert called == []


def test_lock_screen_preserves_response_for_windows_result():
    sent = []
    desktop.lock_screen(
        {"id": "lock-1"}, device_id="windows-1",
        lock_workstation=lambda: 0,
        publish_response=lambda topic, result: sent.append((topic, result)),
        logger=logging.getLogger("test.lock-screen"),
    )
    assert sent == [("lock", {
        "type": "lock", "device_id": "windows-1", "action": "lock",
        "id": "lock-1", "ok": False, "error": "LockWorkStation returned false",
    })]


def test_swap_mouse_converts_flag_and_reports_native_failure():
    sent = []
    calls = []

    def fail(value):
        calls.append(value)
        raise OSError("native failure")

    desktop.prank_swap_mouse(
        {"swap": 0}, device_id="windows-1", swap_mouse_button=fail,
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert calls == [0]
    assert sent == [("prank_swap_mouse", {
        "type": "prank_swap_mouse", "device_id": "windows-1", "ok": False, "swap": False,
    })]


def test_hide_desktop_uses_legacy_shell_view_commands():
    calls = []

    class User32:
        def FindWindowW(self, class_name, title):
            calls.append(("find", class_name, title))
            return 10

        def FindWindowExW(self, parent, _after, class_name, title):
            calls.append(("find_ex", parent, class_name, title))
            return {"SHELLDLL_DefView": 20, "SysListView32": 30}[class_name]

        def ShowWindow(self, handle, command):
            calls.append(("show", handle, command))

    sent = []
    desktop.prank_hide_desktop(
        {"hide": False}, device_id="windows-1", user32=User32(),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert calls[-1] == ("show", 30, 5)
    assert sent == [("prank_hide_desktop", {
        "type": "prank_hide_desktop", "device_id": "windows-1", "ok": True, "hide": False,
    })]
