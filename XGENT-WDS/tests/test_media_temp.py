from __future__ import annotations

import os
from pathlib import Path

import media_temp
import xgent_wds as agent


def test_cleanup_only_removes_expired_owned_media_files(tmp_path, monkeypatch):
    monkeypatch.setattr(media_temp, "_media_temp_root", lambda create=True: tmp_path)
    expired = tmp_path / "xider-media-camera.ogg"
    fresh = tmp_path / "xider-media-fresh.png"
    unrelated = tmp_path / "other-app.ogg"
    folder = tmp_path / "xider-media-folder.png"
    expired.write_bytes(b"old")
    fresh.write_bytes(b"new")
    unrelated.write_bytes(b"keep")
    folder.mkdir()
    now = 2_000_000_000.0
    os.utime(expired, (now - media_temp.MEDIA_TEMP_TTL_SECONDS - 1,) * 2)
    os.utime(fresh, (now - 60,) * 2)
    os.utime(unrelated, (now - media_temp.MEDIA_TEMP_TTL_SECONDS * 10,) * 2)

    report = media_temp.cleanup_stale_media_temp(now=now)

    assert report == {"state": "success", "scanned": 3, "deleted": 1, "skipped": 2, "failed": 0}
    assert not expired.exists()
    assert fresh.exists() and unrelated.exists() and folder.is_dir()


def test_cleanup_does_not_follow_or_delete_symlinked_media(tmp_path, monkeypatch):
    monkeypatch.setattr(media_temp, "_media_temp_root", lambda create=True: tmp_path)
    target = tmp_path.parent / "keep.bin"
    target.write_bytes(b"keep")
    link = tmp_path / "xider-media-link.ogg"
    try:
        link.symlink_to(target)
    except OSError:
        return
    now = 2_000_000_000.0
    report = media_temp.cleanup_stale_media_temp(now=now)
    assert report["deleted"] == 0
    assert target.read_bytes() == b"keep"
    assert link.is_symlink()


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


def test_media_temp_creation_enforces_file_and_total_byte_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(media_temp, "_media_temp_root", lambda create=True: tmp_path)
    monkeypatch.setattr(media_temp, "_MAX_FILE_BYTES", 4)
    monkeypatch.setattr(media_temp, "_MAX_TOTAL_BYTES", 8)

    with media_temp.create_media_temp_file(".wav") as first:
        assert first.write(b"1234") == 4
        try:
            first.write(b"5")
        except OSError as exc:
            assert "quota" in str(exc).lower()
        else:
            raise AssertionError("a media file must not exceed its per-file byte limit")

    with media_temp.create_media_temp_file(".png") as second:
        second.write(b"5678")

    try:
        media_temp.create_media_temp_file(".ogg")
    except OSError as exc:
        assert "quota" in str(exc).lower()
    else:
        raise AssertionError("new files must reserve space within the XIDER media-temp byte limit")


def test_external_writer_reservation_catches_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(media_temp, "_media_temp_root", lambda create=True: tmp_path)
    monkeypatch.setattr(media_temp, "_MAX_FILE_BYTES", 4)
    monkeypatch.setattr(media_temp, "_MAX_TOTAL_BYTES", 8)

    temp = media_temp.create_media_temp_file(".png")
    path = Path(temp.name)
    temp.close(retain_reservation=True)
    path.write_bytes(b"12345")

    try:
        temp.release()
    except OSError as exc:
        assert "quota" in str(exc).lower()
    else:
        raise AssertionError("an external writer must not leave an oversized XIDER media file")

    assert not path.exists()


def test_cleanup_command_is_advertised_and_returns_only_bounded_report(monkeypatch):
    sent = []
    monkeypatch.setattr(media_temp, "cleanup_stale_media_temp", lambda: {
        "state": "success", "scanned": 4, "deleted": 2, "skipped": 2, "failed": 0,
    })
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, result: sent.append((topic, result)) or True

    client._do_media_temp_cleanup({"id": "request-123"})

    assert "media_temp_cleanup" in agent.SUPPORTED_COMMANDS
    assert sent[0][0] == "media_temp_cleanup"
    result = sent[0][1]
    assert result["feature_id"] == "D02-F23"
    assert result["request_id"] == "request-123"
    assert result["data"] == {"state": "success", "scanned": 4, "deleted": 2, "skipped": 2, "failed": 0}
    assert "path" not in str(result).lower()
