import base64
import logging
import os

from xg_commands import filesystem


def test_file_get_preserves_legacy_aliases_and_success_shape(tmp_path):
    source = tmp_path / "sample.bin"
    source.write_bytes(b"sample-content")
    sent = []

    filesystem.file_get(
        {"path": str(source)}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        getsize=lambda path: source.stat().st_size, open_file=open,
        basename=os.path.basename,
        base64_module=base64, logger=logging.getLogger("test.file-get"),
    )

    topic, result = sent[0]
    encoded = base64.b64encode(b"sample-content").decode("ascii")
    assert topic == "file_get"
    assert result == {
        "type": "file_get", "device_id": "windows-1", "ok": True,
        "path": str(source), "b64": encoded, "name": "sample.bin",
        "data": encoded, "filename": "sample.bin",
    }


def test_file_get_rejects_oversized_file_without_opening():
    sent = []

    def forbidden_open(*args):
        raise AssertionError("oversized files must not be read")

    filesystem.file_get(
        {"path": "large.bin"}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        getsize=lambda _path: 45_000_001, open_file=forbidden_open,
        basename=lambda path: path, base64_module=base64,
        logger=logging.getLogger("test.file-get"),
    )

    assert sent == [("file_get", {
        "type": "file_get", "device_id": "windows-1", "ok": False,
        "error": "файл 45000001 байт > лимит",
    })]


def test_file_get_ignores_empty_path_and_reports_io_errors():
    sent = []
    filesystem.file_get(
        {"path": "  "}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        getsize=lambda _path: 0, open_file=open, basename=lambda path: path,
        base64_module=base64, logger=logging.getLogger("test.file-get"),
    )
    assert sent == []

    filesystem.file_get(
        {"path": "missing.bin"}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        getsize=lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
        open_file=open, basename=lambda path: path, base64_module=base64,
        logger=logging.getLogger("test.file-get"),
    )
    assert sent[-1] == ("file_get", {
        "type": "file_get", "device_id": "windows-1", "ok": False,
        "error": "missing",
    })


def test_file_del_reports_invalid_path_and_remove_error():
    sent = []
    filesystem.file_del(
        {"path": []}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        expanduser=lambda path: path, expandvars=lambda path: path,
        remove=lambda _path: None,
    )
    assert sent[-1] == ("file_del", {
        "type": "file_del", "device_id": "windows-1", "ok": False,
        "state": "failed", "error": "invalid_or_missing_path",
    })

    filesystem.file_del(
        {"path": "missing.bin"}, device_id="windows-1",
        publish_response=lambda topic, result: sent.append((topic, result)),
        expanduser=lambda path: path, expandvars=lambda path: path,
        remove=lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    assert sent[-1] == ("file_del", {
        "type": "file_del", "device_id": "windows-1", "ok": False,
        "path": "missing.bin", "error": "missing",
    })


def test_find_file_skips_search_for_empty_pattern_and_preserves_result_shape():
    sent = []
    calls = []
    filesystem.find_file(
        {"pattern": "  ", "root": "~/docs"}, device_id="windows-1",
        find_matches=lambda *args: calls.append(args),
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert calls == []
    assert sent == [("find_file", {
        "type": "find_file", "device_id": "windows-1",
        "ok": False, "matches": [], "total": 0,
    })]

    filesystem.find_file(
        {"pattern": " report ", "root": " ~/docs "}, device_id="windows-1",
        find_matches=lambda *args: {"ok": True, "matches": ["report.txt"], "total": 1, "path": args},
        publish_response=lambda topic, result: sent.append((topic, result)),
    )
    assert sent[-1][1]["path"] == ("report", "~/docs")
