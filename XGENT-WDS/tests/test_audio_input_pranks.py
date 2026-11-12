from types import SimpleNamespace

from xg_commands import pranks


class InlineThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_tts_scenes_keep_settings_phrase_and_responses():
    calls, sent, logs = [], [], []

    class Engine:
        def setProperty(self, *args):
            calls.append(("property", *args))

        def say(self, phrase):
            calls.append(("say", phrase))

        def runAndWait(self):
            calls.append(("run",))

    engine_module = SimpleNamespace(init=lambda: Engine())
    pranks.say_whisper(
        {"text": "test whisper"}, device_id="d", publish_response=lambda *args: sent.append(args),
        threading_module=SimpleNamespace(Thread=InlineThread), speech_engine_module=engine_module,
    )
    assert calls == [("property", "rate", 90), ("property", "volume", 0.7), ("say", "test whisper"), ("run",)]
    assert "test whisper" in sent[0][1]["text"]

    calls.clear()
    pranks.laugh_track(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        threading_module=SimpleNamespace(Thread=InlineThread), logger=SimpleNamespace(error=logs.append),
        speech_engine_module=engine_module,
    )
    assert calls == [
        ("property", "rate", 140), ("property", "volume", 1.0),
        ("say", "Ха ха ха ха! Мва ха ха ха ха! Я контролирую этот компьютер!"), ("run",),
    ]
    assert sent[-1][1]["type"] == "prank_laugh_track"
    assert logs == []


def test_random_beeps_keep_eight_attempts_and_timing_even_if_beep_fails():
    beeps, sleeps, sent = [], [], []
    values = iter([600, 100] * 8)
    random_module = SimpleNamespace(
        randint=lambda _low, _high: next(values), uniform=lambda low, high: (low + high) / 2,
    )
    pranks.random_beeps(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        random_module=random_module, time_module=SimpleNamespace(sleep=sleeps.append),
        threading_module=SimpleNamespace(Thread=InlineThread),
        winsound_module=SimpleNamespace(Beep=lambda *args: beeps.append(args)),
    )
    assert beeps == [(600, 100)] * 8
    assert sleeps == [0.25] * 8
    assert sent[0][1]["type"] == "prank_random_beeps"


def test_caps_disco_and_alert_loop_preserve_native_call_patterns():
    key_events, messages, sleeps, sent = [], [], [], []
    user32 = SimpleNamespace(
        keybd_event=lambda *args: key_events.append(args),
        MessageBoxW=lambda *args: messages.append(args),
    )
    threading = SimpleNamespace(Thread=InlineThread)
    publish = lambda *args: sent.append(args)
    pranks.caps_disco(
        {}, device_id="d", publish_response=publish,
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=user32)),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=threading,
    )
    assert len(key_events) == 64
    assert key_events[:4] == [(0x14, 0, 0, 0), (0x14, 0, 2, 0), (0x90, 0, 0, 0), (0x90, 0, 2, 0)]
    assert sleeps == [0.18] * 16
    sleeps.clear()
    pranks.alert_loop(
        {}, device_id="d", publish_response=publish,
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=user32)),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=threading,
    )
    assert [call[1] for call in messages] == [
        "Хотите ли вы удалить Интернет для освобождения памяти?",
        "Вы уверены? Это действие невозможно отменить!",
        "Отмена операции: Интернет слишком тяжелый.",
    ]
    assert sleeps == [0.3, 0.3]
    assert [item[1]["type"] for item in sent] == ["prank_caps_disco", "prank_alert_loop"]
