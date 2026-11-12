from types import SimpleNamespace

from xg_commands import pranks


class InlineThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def _window_api(moves):
    class User32:
        def GetForegroundWindow(self):
            return 17

        def GetWindowRect(self, _hwnd, rect):
            rect.left, rect.top, rect.right, rect.bottom = 100, 200, 500, 600
            return 1

        def MoveWindow(self, *args):
            moves.append(args)
            return 1

    return SimpleNamespace(
        Structure=object, c_long=int, byref=lambda value: value,
        windll=SimpleNamespace(user32=User32()),
    )


def test_window_shake_and_earthquake_restore_original_position():
    moves, sleeps, sent = [], [], []
    ctypes_module = _window_api(moves)
    publish = lambda *args: sent.append(args)
    pranks.shake_window(
        {}, device_id="d", publish_response=publish, ctypes_module=ctypes_module,
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=SimpleNamespace(Thread=InlineThread),
    )
    assert len(moves) == 25
    assert moves[0] == (17, 120, 200, 400, 400, True)
    assert moves[-1] == (17, 100, 200, 400, 400, True)
    assert sleeps == [0.06] * 24

    moves.clear()
    sleeps.clear()
    pranks.earthquake(
        {}, device_id="d", publish_response=publish, ctypes_module=ctypes_module,
        random_module=SimpleNamespace(randint=lambda _low, _high: 5),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=SimpleNamespace(Thread=InlineThread),
    )
    assert len(moves) == 41
    assert moves[0] == (17, 105, 205, 400, 400, True)
    assert moves[-1] == (17, 100, 200, 400, 400, True)
    assert sleeps == [0.04] * 40
    assert [call[1]["type"] for call in sent] == ["prank_shake_window", "prank_earthquake"]


class FakeWidget:
    def __init__(self, *args, **kwargs):
        self.options = kwargs

    def pack(self, **_kwargs):
        pass

    def place(self, **_kwargs):
        pass

    def config(self, **_kwargs):
        pass


class FakeRoot(FakeWidget):
    def __init__(self):
        super().__init__()
        self.bindings, self.timers = {}, []
        self.title_text = None

    def title(self, value):
        self.title_text = value

    def attributes(self, *_args):
        pass

    def configure(self, **_kwargs):
        pass

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback

    def after(self, delay, callback):
        self.timers.append((delay, callback))

    def mainloop(self):
        pass

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def destroy(self):
        pass


def test_fullscreen_prank_scenes_keep_escape_and_timeout_exits():
    root = FakeRoot()
    tkinter_module = SimpleNamespace(Tk=lambda: root, Frame=FakeWidget, Label=FakeWidget, Canvas=FakeWidget)
    started = []
    sent, logs = [], []

    class StagedThread:
        def __init__(self, *, target, name=None, **_kwargs):
            self.target, self.name = target, name

        def start(self):
            started.append(self.name)
            if self.name == "xgent-bsod":
                self.target()

    pranks.bsod(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(SetCursorPos=lambda *_: 1))),
        time_module=SimpleNamespace(sleep=lambda _seconds: None),
        threading_module=SimpleNamespace(Thread=StagedThread),
        logger=SimpleNamespace(error=lambda *args: logs.append(args)), tkinter_module=tkinter_module,
    )
    assert root.title_text == "System Fatal Error"
    assert set(root.bindings) == {"<Escape>", "<Double-Button-1>"}
    assert [delay for delay, _callback in root.timers] == [1200, 18000]
    assert started == ["xgent-bsod", None]

    root = FakeRoot()
    tkinter_module.Tk = lambda: root
    pranks.fake_ransom_cats(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        threading_module=SimpleNamespace(Thread=InlineThread),
        logger=SimpleNamespace(error=lambda *args: logs.append(args)),
        tkinter_module=tkinter_module,
    )
    assert root.title_text == "RansomCat v1.0"
    assert set(root.bindings) == {"<Escape>", "<Double-Button-1>"}
    assert [delay for delay, _callback in root.timers] == [18000]
    assert [call[1]["type"] for call in sent] == ["prank_bsod", "prank_fake_ransom_cats"]
    assert logs == []
