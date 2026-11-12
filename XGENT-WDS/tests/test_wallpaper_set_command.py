import base64
import logging
import os
import random
import tempfile
from types import SimpleNamespace
from urllib import request

from xg_commands import display


def test_wallpaper_set_uses_supplied_image_and_keeps_legacy_responses(tmp_path):
    current_wallpaper = tmp_path / "current.jpg"
    current_wallpaper.write_bytes(b"old wallpaper")
    calls = []

    class User32:
        def SystemParametersInfoW(self, action, _param, value, _flags):
            calls.append((action, value))
            if action == 0x0073:
                value.value = str(current_wallpaper)
            return 1

    class Ctypes:
        windll = SimpleNamespace(user32=User32())

        @staticmethod
        def create_unicode_buffer(_size):
            return SimpleNamespace(value="")

    sent = []
    display.wallpaper_set(
        {"b64": base64.b64encode(b"new wallpaper").decode("ascii")},
        device_id="windows-1", config_dir=tmp_path, ctypes_module=Ctypes,
        os_module=os, tempfile_module=SimpleNamespace(gettempdir=lambda: str(tmp_path)),
        base64_module=base64, random_module=random, request_module=request,
        open_file=open, publish_response=lambda topic, result: sent.append((topic, result)),
        logger=logging.getLogger("test.wallpaper"),
    )

    target = tmp_path / "xgent_wallpaper.jpg"
    assert target.read_bytes() == b"new wallpaper"
    assert (tmp_path / "backup_wallpaper.txt").read_text(encoding="utf-8") == str(current_wallpaper)
    assert [call[0] for call in calls] == [0x0073, 0x0014]
    assert sent[0] == ("wallpaper_set", {
        "type": "wallpaper_set", "device_id": "windows-1", "ok": True, "error": None,
    })
    assert sent[1][0] == "output" and sent[1][1]["ok"] is True


def test_wallpaper_set_preserves_error_response_for_invalid_base64(tmp_path):
    sent = []
    display.wallpaper_set(
        {"b64": "a"}, device_id="windows-1", config_dir=tmp_path,
        ctypes_module=SimpleNamespace(), os_module=os,
        tempfile_module=SimpleNamespace(gettempdir=lambda: str(tmp_path)),
        base64_module=base64, random_module=random, request_module=request,
        open_file=open, publish_response=lambda topic, result: sent.append((topic, result)),
        logger=logging.getLogger("test.wallpaper"),
    )

    assert sent[0][0] == "wallpaper_set"
    assert sent[0][1]["ok"] is False
    assert sent[0][1]["error"] == "Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4"
    assert sent[1][0] == "output" and sent[1][1]["ok"] is False
