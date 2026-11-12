from types import SimpleNamespace

from xg_commands import pranks


class InlineThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_browser_memes_preserve_urls_order_and_spacing():
    urls, sleeps, sent = [], [], []
    pranks.open_browser_memes(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        webbrowser_module=SimpleNamespace(open_new_tab=urls.append),
        time_module=SimpleNamespace(sleep=sleeps.append),
    )
    assert urls == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://cat-bounce.com/", "https://heeeeeeeey.com/",
    ]
    assert sleeps == [0.3] * 3
    assert sent[0][1]["type"] == "prank_open_browser_memes" and sent[0][1]["ok"] is True


def test_volume_jump_restores_original_level():
    changes, sent = [], []

    class Volume:
        def GetMasterVolumeLevelScalar(self):
            return 0.42

        def SetMasterVolumeLevelScalar(self, value, _context):
            changes.append(value)

    pranks.volume_jump(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        get_master_volume=Volume, time_module=SimpleNamespace(sleep=lambda _seconds: None),
        threading_module=SimpleNamespace(Thread=InlineThread),
    )
    assert changes == [1.0, 0.1] * 4 + [0.42]
    assert sent[0][1]["type"] == "prank_volume_jump"


def test_speak_time_keeps_rate_phrase_and_async_scene():
    calls, sent = [], []

    class Engine:
        def setProperty(self, *args):
            calls.append(("property", *args))

        def say(self, phrase):
            calls.append(("say", phrase))

        def runAndWait(self):
            calls.append(("run",))

    pranks.speak_time(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        time_module=SimpleNamespace(strftime=lambda _fmt: "12 часов 34 минут"),
        threading_module=SimpleNamespace(Thread=InlineThread),
        speech_engine_module=SimpleNamespace(init=lambda: Engine()),
    )
    phrase = "Внимание! Точное время на вашей планете: 12 часов 34 минут. Не забудьте сделать разминку!"
    assert calls == [("property", "rate", 140), ("say", phrase), ("run",)]
    assert phrase in sent[0][1]["text"]


def test_ghost_typer_uses_selected_phrase_and_unicode_key_events():
    events, sleeps, sent = [], [], []
    phrase = "   👻 Привет... Я живу в твоем кулере...   "
    pranks.ghost_typer(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(
            keybd_event=lambda *args: events.append(args),
        ))),
        random_module=SimpleNamespace(choice=lambda _phrases: phrase),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=SimpleNamespace(Thread=InlineThread),
        logger=SimpleNamespace(error=lambda *_args: None),
    )
    assert events[:2] == [(0, ord(phrase[0]), 0x0004, 0), (0, ord(phrase[0]), 0x0006, 0)]
    assert len(events) == len(phrase) * 2
    assert sleeps == [1.0] + [0.06] * len(phrase)
    assert sent[0][1]["type"] == "prank_ghost_typer"
