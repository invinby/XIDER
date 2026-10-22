from __future__ import annotations

import os
from pathlib import Path

import media_temp
import xgent_mcs as agent


def test_cleanup_only_removes_expired_owned_media_files(tmp_path, monkeypatch):
    monkeypatch.setattr(media_temp, "_media_temp_root", lambda create=True: tmp_path)
    expired = tmp_path / "xider-media-camera.ogg"
    fresh = tmp_path / "xider-media-fresh.png"
    unrelated = tmp_path / "other-app.ogg"
    expired.write_bytes(b"old")
    fresh.write_bytes(b"new")
    unrelated.write_bytes(b"keep")
    now = 2_000_000_000.0
    os.utime(expired, (now - media_temp.MEDIA_TEMP_TTL_SECONDS - 1,) * 2)
    os.utime(fresh, (now - 60,) * 2)
    os.utime(unrelated, (now - media_temp.MEDIA_TEMP_TTL_SECONDS * 10,) * 2)

    report = media_temp.cleanup_stale_media_temp(now=now)

    assert report == {"state": "success", "scanned": 2, "deleted": 1, "skipped": 1, "failed": 0}
    assert not expired.exists()
    assert fresh.exists() and unrelated.exists()


def test_creation_rejects_symlinked_xider_app_root_before_writing(tmp_path, monkeypatch):
    base = tmp_path / "local-app-data"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    app_root = base / "XIDER"
    try:
        app_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    try:
        media_temp.create_media_temp_file(".png")
    except OSError:
        pass
    else:
        raise AssertionError("must reject redirected XIDER app-data root")

    assert not (outside / "media-temp").exists()


def test_cleanup_command_is_advertised_and_returns_only_bounded_report(monkeypatch):
    sent = []
    monkeypatch.setattr(media_temp, "cleanup_stale_media_temp", lambda: {
        "state": "success", "scanned": 3, "deleted": 1, "skipped": 2, "failed": 0,
    })
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_media_temp_cleanup({"id": "request-456"})

    assert "media_temp_cleanup" in agent.SUPPORTED_COMMANDS
    assert sent[0][0] == "media_temp_cleanup"
    result = sent[0][1]
    assert result["feature_id"] == "D02-F23"
    assert result["request_id"] == "request-456"
    assert result["data"] == {"state": "success", "scanned": 3, "deleted": 1, "skipped": 2, "failed": 0}
    assert "path" not in str(result).lower()
