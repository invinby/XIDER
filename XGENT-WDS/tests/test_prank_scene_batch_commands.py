from types import SimpleNamespace

from xg_commands import pranks


class InlineThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_toast_spam_starts_the_same_hidden_powershell_script():
    calls, sent = [], []
    proc = SimpleNamespace(Popen=lambda *args, **kwargs: calls.append((args, kwargs)), CREATE_NO_WINDOW=8)
    pranks.toast_spam({}, device_id="device-1", publish_response=lambda *x: sent.append(x), subprocess_module=proc)
    args, kwargs = calls[0]
    assert args[0][:5] == ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command"]
    assert "1..5" in args[0][5] and "Start-Sleep -Milliseconds 700" in args[0][5]
    assert kwargs == {"creationflags": 8}
    assert sent[0][1]["type"] == "prank_toast_spam" and sent[0][1]["ok"] is True


def test_keyboard_disco_keeps_key_order_repetition_and_timing():
    events, sleeps, sent = [], [], []
    user32 = SimpleNamespace(keybd_event=lambda *args: events.append(args))
    pranks.keyboard_disco(
        {}, device_id="device-1", publish_response=lambda *x: sent.append(x),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=user32)),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=SimpleNamespace(Thread=InlineThread),
    )
    assert len(events) == 72
    assert events[:6] == [
        (0x14, 0x45, 1, 0), (0x14, 0x45, 3, 0),
        (0x90, 0x45, 1, 0), (0x90, 0x45, 3, 0),
        (0x91, 0x45, 1, 0), (0x91, 0x45, 3, 0),
    ]
    assert sleeps == [0.12] * 36
    assert sent[0][1]["type"] == "prank_keyboard_disco"


def test_beep_morse_and_sound_sequences_keep_existing_patterns():
    beeps, sleeps, sent = [], [], []
    winsound = SimpleNamespace(Beep=lambda *args: beeps.append(args))
    threading = SimpleNamespace(Thread=InlineThread)
    time = SimpleNamespace(sleep=sleeps.append)
    publish = lambda *args: sent.append(args)
    pranks.beep_morse({}, device_id="d", publish_response=publish, winsound_module=winsound,
                      time_module=time, threading_module=threading)
    assert beeps == [(1000, 150)] * 3 + [(1000, 400)] * 3 + [(1000, 150)] * 3
    assert sleeps == [0.08] * 9
    beeps.clear()
    pranks.sound_spooky({}, device_id="d", publish_response=publish, winsound_module=winsound,
                        threading_module=threading)
    assert beeps == [(freq, 350) for freq in (300, 315, 290, 420, 260, 240, 500, 200)]
    beeps.clear()
    pranks.sound_fart({}, device_id="d", publish_response=publish, winsound_module=winsound,
                      threading_module=threading)
    assert beeps == [(freq, 80) for freq in (180, 160, 140, 120, 110, 95, 80)]
    assert [item[1]["type"] for item in sent] == ["prank_beep_morse", "prank_sound_spooky", "prank_sound_fart"]


def test_notepad_hacker_typer_minimize_and_calculator_keep_outputs():
    sent, spawned, opened, sleeps = [], [], [], []
    process = SimpleNamespace(Popen=lambda *args, **kwargs: spawned.append((args, kwargs)), CREATE_NO_WINDOW=4)
    publish = lambda *args: sent.append(args)
    pranks.open_notepad_type({"text": "a'b\"c"}, device_id="d", publish_response=publish,
                             subprocess_module=process)
    ps = spawned[-1][0][0][-1]
    assert "a b c" in ps
    assert sent[-1][1]["type"] == "prank_open_notepad_type"
    pranks.hacker_typer({}, device_id="d", publish_response=publish,
                        webbrowser_module=SimpleNamespace(open=opened.append))
    assert opened == ["https://hackertyper.net"]
    pranks.minimize_all({}, device_id="d", publish_response=publish, subprocess_module=process)
    assert "MinimizeAll" in spawned[-1][0][0][-1]
    pranks.open_calc_spam({}, device_id="d", publish_response=publish,
                          subprocess_module=process, time_module=SimpleNamespace(sleep=sleeps.append))
    assert spawned[-5:] == [(('calc.exe',), {"shell": True})] * 5
    assert sleeps == [0.2] * 5
    assert [item[1]["type"] for item in sent] == [
        "prank_open_notepad_type", "prank_hacker_typer", "prank_minimize_all", "prank_open_calc_spam",
    ]


def test_pointer_scene_handlers_preserve_restore_and_motion_contracts():
    import ctypes
    import threading

    from xg_commands import pranks

    sent, angles, callbacks, speeds, moves = [], [], {}, [], []

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            pass

    pranks.invert_screen(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        read_current_angle=lambda: 90,
        rotate_screen=lambda angle: angles.append(angle) or (True, "ok"),
        threading_module=SimpleNamespace(Event=threading.Event, Thread=DeferredThread),
        time_module=SimpleNamespace(time_ns=lambda: 1),
        register_cancel=lambda name, callback: callbacks.setdefault(name, callback),
        finish_cancel=lambda _name: None,
    )
    assert angles == [180]
    callbacks.popitem()[1]()
    assert angles == [180, 90]

    def system_parameters(action, _param, value, _flags):
        if action == 0x0070:
            ctypes.cast(value, ctypes.POINTER(ctypes.c_uint)).contents.value = 17
        else:
            speeds.append(int(value))
        return 1

    callbacks.clear()
    pranks.slow_mouse(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(
            user32=SimpleNamespace(SystemParametersInfoW=system_parameters),
        ), c_uint=ctypes.c_uint, byref=ctypes.byref),
        threading_module=SimpleNamespace(Event=threading.Event, Lock=threading.Lock, Thread=DeferredThread),
        time_module=SimpleNamespace(time_ns=lambda: 2),
        register_cancel=lambda name, callback: callbacks.setdefault(name, callback),
        finish_cancel=lambda _name: None,
    )
    assert speeds == [1]
    callbacks.popitem()[1]()
    assert speeds == [1, 17]

    user32 = SimpleNamespace(
        GetCursorPos=lambda point: (setattr(point, "x", 10) or setattr(point, "y", 20) or True),
        SetCursorPos=lambda x, y: moves.append((x, y)),
    )
    threading = SimpleNamespace(Thread=InlineThread)
    pranks.random_cursor_motion(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(Structure=object, c_long=int, byref=lambda point: point,
                                      windll=SimpleNamespace(user32=user32)),
        random_module=SimpleNamespace(randint=lambda _low, _high: 3),
        time_module=SimpleNamespace(sleep=lambda _seconds: None), threading_module=threading,
    )
    assert moves == [(13, 23)] * 25
    assert sent[-1][1]["state"] == "launch_accepted"
    assert sent[-1][1]["effect_confirmed"] is False
