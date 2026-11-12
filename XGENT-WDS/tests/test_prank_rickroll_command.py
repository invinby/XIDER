import logging
import sys
import ctypes
from types import SimpleNamespace

from xg_commands import pranks


def test_rickroll_clamps_tabs_and_reports_only_browser_acceptance():
    opened = []
    delays = []
    sent = []
    outcomes = iter([True, False, True])

    pranks.rickroll(
        {"tabs": 3, "id": "prank-1"}, device_id="windows-1",
        open_new_tab=lambda url: opened.append(url) or next(outcomes),
        sleep=delays.append,
        publish_response=lambda topic, result: sent.append((topic, result)),
        logger=logging.getLogger("test.prank-rickroll"),
    )

    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] * 3
    assert delays == [0.08, 0.08]
    assert sent[0][1]["state"] == "launch_accepted"
    assert sent[0][1]["tabs_requested"] == 3
    assert sent[0][1]["tabs_accepted"] == 2
    assert sent[0][1]["tabs_failed"] == 1
    assert sent[0][1]["id"] == "prank-1"


def test_rickroll_uses_legacy_default_when_tab_count_is_invalid():
    opened = []
    delays = []
    sent = []
    pranks.rickroll(
        {"tabs": "bad"}, device_id="windows-1",
        open_new_tab=lambda _url: opened.append(1) or False,
        sleep=delays.append,
        publish_response=lambda topic, result: sent.append((topic, result)),
        logger=logging.getLogger("test.prank-rickroll"),
    )
    assert len(opened) == 10
    assert len(delays) == 9
    assert sent[0][1]["state"] == "failed"


def test_screamer_keeps_thread_name_and_effect_order():
    events = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            events.append(("thread", name, daemon))

        def start(self):
            self.target()

    class Volume:
        def SetMasterVolumeLevelScalar(self, value, _context):
            events.append(("volume", value))

        def SetMute(self, value, _context):
            events.append(("mute", value))

    sent = []
    pranks.screamer(
        device_id="windows-1", get_master_volume=lambda: Volume(),
        open_new=lambda url: events.append(("url", url)),
        beep=lambda frequency, duration: events.append(("beep", frequency, duration)),
        threading_module=type("Threading", (), {"Thread": Thread}),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )

    assert events[:4] == [
        ("thread", "xgent-screamer", True), ("volume", 1.0), ("mute", False),
        ("url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ]
    assert events[4:] == [("beep", 2500, 400)] * 5
    assert sent == [("prank_screamer", {"type": "prank_screamer", "device_id": "windows-1", "ok": True})]


def test_siren_caps_duration_and_preserves_beep_pattern():
    events = []
    times = iter([0, 0, 0, 31])

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            events.append(("thread", name, daemon))

        def start(self):
            self.target()

    sent = []
    pranks.siren(
        {"duration": 90}, device_id="windows-1",
        time_module=type("Time", (), {
            "time": staticmethod(lambda: next(times)),
            "sleep": staticmethod(lambda duration: events.append(("sleep", duration))),
        }),
        beep=lambda frequency, duration: events.append(("beep", frequency, duration)),
        threading_module=type("Threading", (), {"Thread": Thread}),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert events == [
        ("thread", "xgent-siren", True), ("beep", 1200, 200), ("beep", 700, 200),
        ("beep", 1200, 200), ("beep", 700, 200),
    ]
    assert sent[0][1]["duration"] == 30


def test_shout_tts_keeps_lazy_import_thread_and_text_limit(monkeypatch):
    events = []

    class Engine:
        def setProperty(self, name, value):
            events.append(("property", name, value))

        def say(self, text):
            events.append(("say", text))

        def runAndWait(self):
            events.append(("run",))

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            events.append(("thread", name, daemon))

        def start(self):
            self.target()

    monkeypatch.setitem(sys.modules, "pyttsx3", SimpleNamespace(init=lambda: Engine()))
    sent = []
    text = "я" * 350
    pranks.shout_tts(
        {"text": text}, device_id="windows-1",
        threading_module=type("Threading", (), {"Thread": Thread}),
        publish_response=lambda topic, result: sent.append((topic, result)),
        logger=logging.getLogger("test.prank-shout"),
    )
    assert events[0] == ("thread", "xgent-shout", True)
    assert events[1:] == [("property", "volume", 1.0), ("say", text[:300]), ("run",)]
    assert sent == [("prank_shout_tts", {
        "type": "prank_shout_tts", "device_id": "windows-1", "ok": True,
    })]


def test_crazy_cursor_caps_duration_and_keeps_position_loop():
    positions = []
    sleeps = []
    sent = []
    times = iter([0, 0, 0, 31])

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.info = (name, daemon)

        def start(self):
            self.target()

    pranks.crazy_cursor(
        {"duration": 120}, device_id="windows-1",
        user32=SimpleNamespace(SetCursorPos=lambda x, y: positions.append((x, y))),
        time_module=SimpleNamespace(time=lambda: next(times), sleep=sleeps.append),
        threading_module=SimpleNamespace(Thread=Thread),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert positions == [(1050, 500), (1042, 561)]
    assert sleeps == [0.02, 0.02]
    assert sent[0][1]["duration"] == 30


def test_matrix_keeps_command_line_and_launch_only_acknowledgement():
    calls = []
    sent = []
    pranks.matrix(
        {"id": "matrix-1"}, device_id="windows-1",
        popen=lambda *args, **kwargs: calls.append((args, kwargs)),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    command, = calls[0][0]
    assert calls[0][1] == {"shell": True}
    assert command.startswith('start cmd /k "color 0a && title MATRIX')
    assert sent == [("prank_matrix", {
        "type": "prank_matrix", "device_id": "windows-1", "id": "matrix-1",
        "ok": True, "state": "launch_accepted", "effect_confirmed": False,
    })]


def test_dancing_windows_restores_original_position_after_loop():
    moves = []
    sent = []

    class User32:
        def GetForegroundWindow(self):
            return 42

        def GetWindowRect(self, _hwnd, pointer):
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = 10, 20, 210, 120

        def MoveWindow(self, *args):
            moves.append(args)

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.info = (name, daemon)

        def start(self):
            self.target()

    pranks.dancing_windows(
        {"duration": -1}, device_id="windows-1",
        ctypes_module=SimpleNamespace(
            windll=SimpleNamespace(user32=User32()), Structure=ctypes.Structure,
            c_long=ctypes.c_long, byref=ctypes.byref,
        ),
        time_module=SimpleNamespace(time=lambda: 0, sleep=lambda _duration: None),
        threading_module=SimpleNamespace(Thread=Thread),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert moves == [(42, 10, 20, 200, 100, True)]
    assert sent == [("prank_dancing_windows", {
        "type": "prank_dancing_windows", "device_id": "windows-1", "ok": True,
    })]


def test_black_screen_reports_window_message_acceptance_without_claiming_effect():
    calls = []
    sent = []
    ctypes_module = SimpleNamespace(
        c_size_t=lambda: object(), byref=lambda result: ("ref", result),
        windll=SimpleNamespace(user32=SimpleNamespace(
            SendMessageTimeoutW=lambda *args: calls.append(args) or 1,
        )),
    )
    pranks.black_screen(
        {}, device_id="windows-1", ctypes_module=ctypes_module,
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert calls[0][:6] == (0xFFFF, 0x0112, 0xF170, 2, 0x0002, 3000)
    assert sent[0][1]["state"] == "launch_accepted"
    assert sent[0][1]["effect_confirmed"] is False


def test_random_site_reports_browser_exception_without_leaking_error_text():
    sent = []
    selected = []

    def choose(options):
        selected.extend(options)
        return options[2]

    def fail(_url):
        raise RuntimeError("private browser detail")

    pranks.random_site(
        device_id="windows-1", choice=choose, open_new_tab=fail,
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert len(selected) == 5
    assert sent[0][1]["url"] == "https://theuselessweb.com/"
    assert sent[0][1]["text"].endswith("(RuntimeError).")
    assert "private browser detail" not in str(sent)


def test_msgbox_spam_caps_count_and_text_before_worker_launch():
    calls = []
    sent = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.info = (name, daemon)

        def start(self):
            self.target()

    pranks.msgbox_spam(
        {"text": "x" * 250, "count": 50}, device_id="windows-1",
        show_message_box=lambda *args: calls.append(args),
        threading_module=SimpleNamespace(Thread=Thread),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert len(calls) == 10
    assert all(call == (None, "x" * 200, "XGENT", 0x40) for call in calls)
    assert sent == [("msgbox_spam", {
        "type": "msgbox_spam", "device_id": "windows-1", "ok": True, "count": 10,
    })]
