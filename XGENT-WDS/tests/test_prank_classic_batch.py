from types import SimpleNamespace

from xg_commands import pranks


class InlineThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_fake_virus_preserves_alert_sequence_and_response():
    calls, sent, sleeps = [], [], []
    user32 = SimpleNamespace(MessageBoxW=lambda *args: calls.append(args))
    pranks.fake_virus(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=user32)),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=SimpleNamespace(Thread=InlineThread),
    )
    assert len(calls) == 3 and sleeps == [0.4, 0.4]
    assert "Шутка!" in calls[2][1]
    assert sent[0][1]["type"] == "prank_fake_virus"


def test_open_cd_falls_back_to_beep_and_preserves_success_shape():
    beeps, sent = [], []
    pranks.open_cd(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(winmm=SimpleNamespace(
            mciSendStringW=lambda *_args: (_ for _ in ()).throw(OSError("missing drive")),
        ))),
        winsound_module=SimpleNamespace(Beep=lambda *args: beeps.append(args)),
    )
    assert beeps == [(800, 400)]
    assert sent[0][1]["ok"] is True and "missing drive" in sent[0][1]["text"]


def test_fake_error_spam_runs_twelve_beeps_and_acknowledges():
    beeps, sleeps, sent = [], [], []
    pranks.fake_error_spam(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        time_module=SimpleNamespace(sleep=sleeps.append), threading_module=SimpleNamespace(Thread=InlineThread),
        winsound_module=SimpleNamespace(MessageBeep=beeps.append),
    )
    assert beeps == [0x10] * 12 and sleeps == [0.18] * 12
    assert sent[0][1]["ok"] is True


def test_fake_delete_sys32_only_starts_original_mocked_command():
    started, sent = [], []
    pranks.fake_delete_sys32(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        subprocess_module=SimpleNamespace(Popen=lambda *args, **kwargs: started.append((args, kwargs))),
    )
    assert started[0][1] == {"shell": True}
    assert "dir /s C:\\Windows\\System32" in started[0][0][0]
    assert "JUST A PRANK BY XGENT" in started[0][0][0]
    assert sent[0][1]["type"] == "prank_fake_delete_sys32"


def test_fbi_lock_shows_warning_and_returns_original_ack():
    calls, sent = [], []
    pranks.fbi_lock(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(
            MessageBoxW=lambda *args: calls.append(args),
        ))),
        threading_module=SimpleNamespace(Thread=InlineThread),
    )
    assert len(calls) == 1 and "IP-адрес" in calls[0][1]
    assert sent[0][1]["type"] == "prank_fbi_lock"


def test_cat_invaders_preserves_urls_order_spacing_and_ack():
    opened, sleeps, sent = [], [], []
    pranks.cat_invaders(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        webbrowser_module=SimpleNamespace(open=opened.append), time_module=SimpleNamespace(sleep=sleeps.append),
    )
    assert opened == [
        "https://cataas.com/cat/says/HELLO%20HUMAN", "https://cataas.com/cat/gif", "https://cataas.com/cat",
    ]
    assert sleeps == [0.3] * 3
    assert sent[0][1]["type"] == "prank_cat_invaders" and sent[0][1]["ok"] is True


def test_clipboard_pranks_preserve_default_text_and_reversal():
    copied, sent = [], []
    clipboard = SimpleNamespace(copy=copied.append, paste=lambda: "abc")
    pranks.paste_clipboard_spam(
        {}, device_id="d", publish_response=lambda *args: sent.append(args), clipboard_module=clipboard,
    )
    pranks.type_reversed(
        {}, device_id="d", publish_response=lambda *args: sent.append(args), clipboard_module=clipboard,
    )
    assert copied == ["( ͡° ͜ʖ ͡°) Взлом жопы завершен на 99.9%", "cba"]
    assert [item[1]["type"] for item in sent] == ["prank_paste_clipboard_spam", "prank_type_reversed"]


def test_wallpaper_prank_saves_original_before_setting_demo_image(tmp_path):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Image:
        def save(self, path, format_name):
            assert path == tmp_path / "prank_wall.bmp" and format_name == "BMP"
            path.write_bytes(b"bmp")

    saved, applied, sent = [], [], []
    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_READ=2, OpenKey=lambda *_args: Key(),
        QueryValueEx=lambda *_args: ("C:/wallpaper.bmp", 1),
    )
    ctypes_module = SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(
        SystemParametersInfoW=lambda *args: applied.append(args),
    )))
    pranks.change_wallpaper(
        {}, device_id="d", publish_response=lambda *args: sent.append(args), config_dir=tmp_path,
        winreg_module=winreg, ctypes_module=ctypes_module,
        image_module=SimpleNamespace(Image=SimpleNamespace(new=lambda *_args, **_kwargs: Image()),
                                     ImageDraw=SimpleNamespace(Draw=lambda _image: SimpleNamespace(
                                         text=lambda *args, **kwargs: saved.append((args, kwargs)),
                                     ))),
    )
    assert (tmp_path / "backup_wallpaper.txt").read_text(encoding="utf-8") == "C:/wallpaper.bmp"
    assert (tmp_path / "prank_wall.bmp").read_bytes() == b"bmp"
    assert applied[0][0:2] == (20, 0) and sent[0][1]["ok"] is True


def test_restore_wallpaper_reports_missing_backup(tmp_path):
    assert pranks.restore_wallpaper(
        config_dir=tmp_path, ctypes_module=SimpleNamespace(), os_module=SimpleNamespace(),
        logger=SimpleNamespace(exception=lambda *_args: None),
    ) == (False, "ℹ️ Резервная копия обоев не найдена.")


def test_prank_stop_all_cancels_then_restores_and_reports_partial_state():
    calls, sent = [], []
    pranks.stop_all(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        cancel_active_pranks=lambda: calls.append("cancel") or ["s1", "s2"],
        restore_wallpaper=lambda: calls.append("restore") or (False, "no saved wallpaper"),
        logger=SimpleNamespace(info=lambda *_args: None),
    )
    assert calls == ["cancel", "restore"]
    assert sent[0][1]["state"] == "partial" and "2" in sent[0][1]["text"]


def test_short_pranks_preserve_timer_counts_and_response_types():
    calls, sleeps, sent = [], [], []
    user32 = SimpleNamespace(
        SendMessageW=lambda *args: calls.append(("send", args)),
        mouse_event=lambda *args: calls.append(("mouse", args)),
        GetSystemMetrics=lambda axis: 900 if axis == 0 else 600,
        SetCursorPos=lambda x, y: calls.append(("cursor", x, y)),
    )
    ctypes_module = SimpleNamespace(windll=SimpleNamespace(user32=user32))
    threading_module = SimpleNamespace(Thread=InlineThread)
    time_module = SimpleNamespace(sleep=sleeps.append)
    pranks.screen_off_brief(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=ctypes_module, time_module=time_module, threading_module=threading_module,
    )
    pranks.cursor_circle(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        ctypes_module=ctypes_module, time_module=time_module, threading_module=threading_module,
    )
    assert calls[0] == ("send", (0xFFFF, 0x0112, 0xF170, 2))
    assert calls[1] == ("mouse", (0x0001, 1, 1, 0, 0))
    assert len([item for item in calls if item[0] == "cursor"]) == 80
    assert sleeps == [3] + [0.05] * 80
    assert [item[1]["type"] for item in sent] == ["prank_screen_off_brief", "prank_cursor_circle"]


def test_nyan_stream_and_terminal_keep_their_targets():
    opened, started, sent = [], [], []
    pranks.nyan_stream(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        webbrowser_module=SimpleNamespace(open=opened.append),
    )
    pranks.rickroll_terminal(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        subprocess_module=SimpleNamespace(Popen=lambda *args, **kwargs: started.append((args, kwargs))),
    )
    assert opened == ["https://www.youtube.com/watch?v=QH2-TGUlwu4"]
    assert started[0][1] == {"shell": True} and "parrot.live" in started[0][0][0][-1]
    assert [item[1]["type"] for item in sent] == ["prank_nyan_stream", "prank_rickroll_terminal"]
